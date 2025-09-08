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
        # last alerts to rate-limit notifications: key=(guild_id, category, type)
        self._last_alert: dict[tuple[int, str, str], float] = {}
        # dynamic thresholds
        self.SPIKE_UP = 0.01   # +1% or more in a minute
        self.SPIKE_DOWN = -0.01  # -1% or less in a minute
        self.NEW_HIGH_STEP = 0.005  # announce new high if > last high by 0.5%
        self.ALERT_COOLDOWN = 600.0  # seconds
        # start loop in on_ready
        self._alerts_enabled_at: float = 0.0

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

                # fetch snapshot
                try:
                    current, lower, upper, prev_high, prev_low, open_idx = db.get_index_info(guild.id, date_kst, cat)
                except Exception:
                    # ensure and retry once
                    db.ensure_indices_for_day(guild.id, date_kst)
                    current, lower, upper, prev_high, prev_low, open_idx = db.get_index_info(guild.id, date_kst, cat)

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

                # alerts: spike and new high
                pct = (new_val - current) / current if current > 0 else 0.0
                to_send: list[tuple[str, str, int]] = []  # (type, desc, color)
                if pct >= self.SPIKE_UP:
                    to_send.append(("spike_up", f"{cat.upper()} 지수가 분 단위로 +{pct*100:.2f}% 상승", 0x2ecc71))
                elif pct <= self.SPIKE_DOWN:
                    to_send.append(("spike_down", f"{cat.upper()} 지수가 분 단위로 {pct*100:.2f}% 하락", 0xe74c3c))

                if prev_high is None or new_val > prev_high * (1.0 + self.NEW_HIGH_STEP):
                    to_send.append(("new_high", f"{cat.upper()} 지수 신고점 경신: {new_val:.2f}", 0xf1c40f))

                if to_send:
                    # suppress alerts during warm-up window after bot start
                    if time.time() < getattr(self, '_alerts_enabled_at', 0.0):
                        continue
                    ch_id = db.get_notify_channel(guild.id)
                    if ch_id:
                        ch = self.bot.get_channel(ch_id)
                        if isinstance(ch, (discord.TextChannel, discord.Thread)):
                            for ev_type, desc, color in to_send:
                                key = (guild.id, cat, ev_type)
                                last = self._last_alert.get(key, 0.0)
                                now = time.time()
                                if ev_type == "new_high":
                                    # lighter cooldown for new_high
                                    cooldown = self.ALERT_COOLDOWN / 2
                                else:
                                    cooldown = self.ALERT_COOLDOWN
                                if now - last < cooldown:
                                    continue
                                self._last_alert[key] = now
                                try:
                                    embed = discord.Embed(title="📣 활동 지수 알림", description=desc, color=color)
                                    embed.add_field(name="지수", value=f"{new_val:.2f}")
                                    embed.add_field(name="개장가", value=f"{open_idx:.2f}")
                                    embed.add_field(name="변동", value=f"{(new_val-open_idx)/open_idx*100:.2f}%")
                                    embed.set_footer(text=f"카테고리: {cat} • {date_kst}")
                                    await ch.send(embed=embed)
                                except Exception:
                                    pass

    @minute_tick.before_loop
    async def before_minute_tick(self):
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.minute_tick.is_running():
            self.minute_tick.start()
        # enable alerts 120s after boot
        self._alerts_enabled_at = time.time() + 120.0

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
