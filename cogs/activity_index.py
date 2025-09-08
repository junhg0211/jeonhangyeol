import discord
from discord.ext import commands, tasks
from discord import app_commands

import db
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time


KST = ZoneInfo("Asia/Seoul")


class ActivityIndex(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()
        # per-guild minute counters
        self._counters: dict[int, dict] = {}
        # start loop in on_ready

    # ---------- helpers ----------
    def _g(self, guild_id: int) -> dict:
        g = self._counters.setdefault(
            guild_id,
            {
                'chat_count': 0,
                'react_count': 0,
                'last_msg_ts': None,
                'gap_sum': 0.0,
                'gap_n': 0,
                'voice_set': set(),  # member ids in voice
            },
        )
        return g

    @staticmethod
    def _now_kst() -> datetime:
        return datetime.now(KST)

    @staticmethod
    def _today_kst_str() -> str:
        return datetime.now(KST).strftime("%Y-%m-%d")

    @staticmethod
    def _is_trading(now_kst: datetime) -> bool:
        t = now_kst.time()
        return (t >= datetime.strptime("09:00", "%H:%M").time()) and (t < datetime.strptime("21:00", "%H:%M").time())

    # ---------- events ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        g = self._g(message.guild.id)
        g['chat_count'] += 1
        now = time.time()
        last = g['last_msg_ts']
        if last is not None:
            gap = now - last
            # cap gap to 120s for averaging to avoid huge gaps dominating
            g['gap_sum'] += min(max(gap, 0.0), 120.0)
            g['gap_n'] += 1
        g['last_msg_ts'] = now

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot or not reaction.message.guild:
            return
        g = self._g(reaction.message.guild.id)
        g['react_count'] += 1

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not member.guild:
            return
        g = self._g(member.guild.id)
        s = g['voice_set']
        in_voice_after = after and after.channel is not None
        in_voice_before = before and before.channel is not None
        if in_voice_after and not in_voice_before:
            s.add(member.id)
        elif in_voice_before and not in_voice_after:
            s.discard(member.id)
        elif in_voice_after and in_voice_before and after.channel != before.channel:
            s.add(member.id)

    # ---------- minute loop ----------
    @tasks.loop(seconds=60)
    async def minute_tick(self):
        now_kst = self._now_kst()
        if not self._is_trading(now_kst):
            return

        # ensure today's indices exist per guild
        date_kst = now_kst.strftime("%Y-%m-%d")
        ts = int(time.time())
        for guild in list(self.bot.guilds):
            db.ensure_indices_for_day(guild.id, date_kst)

        # compute per guild/category
        for guild in list(self.bot.guilds):
            g = self._g(guild.id)
            chat = int(g.get('chat_count', 0))
            react = int(g.get('react_count', 0))
            voice_count = int(len(g.get('voice_set', set())))
            avg_gap = (g['gap_sum'] / g['gap_n']) if g.get('gap_n') else 999.0

            # Reset per-minute counters (voice_set persists)
            g['chat_count'] = 0
            g['react_count'] = 0
            g['gap_sum'] = 0.0
            g['gap_n'] = 0

            # scoring & update for each category
            metrics = {
                'chat': (chat, react, voice_count, avg_gap),
                'react': (chat, react, voice_count, avg_gap),
                'voice': (chat, react, voice_count, avg_gap),
            }
            for cat, _m in metrics.items():
                chat_c, react_c, voice_c, gap = _m
                # weights
                if cat == 'chat':
                    a, b, c = 1.0, 0.3, 0.3
                elif cat == 'react':
                    a, b, c = 0.3, 1.0, 0.3
                else:  # voice
                    a, b, c = 0.3, 0.3, 1.0

                # gap bonus: faster chats -> extra score
                gap_bonus = 0.0
                if gap < 60.0:
                    gap_bonus = (60.0 - gap) / 60.0  # 0..1

                base_score = a * chat_c + b * react_c + c * voice_c + 0.5 * gap_bonus
                # baseline 1.0 -> neutral; scale factor S tunes volatility
                S = 8.0
                change_pct = max(-0.02, min(0.02, (base_score - 1.0) / S))

                # fetch bounds/current
                try:
                    current, lower, upper = db.get_index_bounds(guild.id, date_kst, cat)
                except Exception:
                    # ensure and retry once
                    db.ensure_indices_for_day(guild.id, date_kst)
                    current, lower, upper = db.get_index_bounds(guild.id, date_kst, cat)

                new_val = current * (1.0 + change_pct)
                if new_val < lower:
                    new_val = lower
                if new_val > upper:
                    new_val = upper

                db.update_activity_tick(
                    guild_id=guild.id,
                    ts=ts,
                    category=cat,
                    idx_value=new_val,
                    delta=new_val - current,
                    chat_count=chat_c,
                    react_count=react_c,
                    voice_count=voice_c,
                    date_kst=date_kst,
                )

    @minute_tick.before_loop
    async def before_minute_tick(self):
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.minute_tick.is_running():
            self.minute_tick.start()

    # 간단 조회: /지수 확인
    group = app_commands.Group(name="지수", description="활동 지수")

    @group.command(name="확인", description="현재 활동 지수를 확인합니다.")
    async def show_index(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        date_kst = self._today_kst_str()
        try:
            cur_chat, _, _ = db.get_index_bounds(interaction.guild.id, date_kst, 'chat')
            cur_voice, _, _ = db.get_index_bounds(interaction.guild.id, date_kst, 'voice')
            cur_react, _, _ = db.get_index_bounds(interaction.guild.id, date_kst, 'react')
        except Exception:
            db.ensure_indices_for_day(interaction.guild.id, date_kst)
            cur_chat, _, _ = db.get_index_bounds(interaction.guild.id, date_kst, 'chat')
            cur_voice, _, _ = db.get_index_bounds(interaction.guild.id, date_kst, 'voice')
            cur_react, _, _ = db.get_index_bounds(interaction.guild.id, date_kst, 'react')

        embed = discord.Embed(title="📈 활동 지수", color=discord.Color.blue())
        embed.add_field(name="채팅", value=f"{cur_chat:.2f}", inline=True)
        embed.add_field(name="통화", value=f"{cur_voice:.2f}", inline=True)
        embed.add_field(name="반응", value=f"{cur_react:.2f}", inline=True)
        embed.set_footer(text="한국시간 09:00–21:00 분단위 집계")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityIndex(bot))

