import discord
from discord.ext import commands, tasks
from discord import app_commands

import database as db
from zoneinfo import ZoneInfo
from datetime import datetime


class Attendance(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()
        self._last_alert_date_by_guild: dict[int, str] = {}

    group = app_commands.Group(name="출석", description="출석 체크 및 랭킹")
    KST = ZoneInfo("Asia/Seoul")

    @tasks.loop(minutes=1)
    async def _notify_yday_not_today(self):
        now = datetime.now(self.KST)
        if now.hour != 20 or now.minute != 0:
            return
        today = now.strftime("%Y-%m-%d")
        for guild in list(self.bot.guilds):
            try:
                if self._last_alert_date_by_guild.get(guild.id) == today:
                    continue
                uids = db.attendance_yesterday_not_today(guild.id)
                mentions = []
                for uid in uids:
                    m = guild.get_member(uid)
                    if m and not m.bot:
                        mentions.append(m.mention)
                ch_id = db.get_notify_channel(guild.id)
                if ch_id and mentions:
                    ch = self.bot.get_channel(ch_id)
                    if isinstance(ch, (discord.TextChannel, discord.Thread)):
                        msg = (
                            "어제는 출석했지만 오늘은 아직 출석하지 않은 분들!\n"
                            + " ".join(mentions)
                            + "\n20:00 기준 미출석입니다. 출석을 잊지 마세요 ⏰"
                        )
                        try:
                            await ch.send(msg)
                        except Exception:
                            pass
                self._last_alert_date_by_guild[guild.id] = today
            except Exception:
                continue

    @_notify_yday_not_today.before_loop
    async def _before_notify_loop(self):
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()

    @group.command(name="하기", description="오늘 출석하고 보상을 받습니다.")
    async def check_in(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        already, streak, reward, maxs = db.attendance_check_in(interaction.guild.id, interaction.user.id)
        if already:
            await interaction.response.send_message(f"오늘은 이미 출석했습니다. 현재 연속 {streak}일!", ephemeral=False)
            return
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} 출석 완료! 연속 {streak}일, 보상 {reward}원 지급(최대 연속 {maxs}일)", ephemeral=False
        )

    @group.command(name="오늘", description="오늘 출석 현황(한 번이라도 출석한 적 있는 유저 기준)")
    @app_commands.describe(상위="표시 인원(기본 20)")
    async def today_board(self, interaction: discord.Interaction, 상위: int = 20):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        checked, not_checked = db.attendance_today(interaction.guild.id)
        topn = max(1, min(int(상위), 50))
        def resolve(uids):
            out = []
            for uid, streak in uids[:topn]:
                m = interaction.guild.get_member(uid)
                if not m:
                    continue  # skip users no longer in guild
                name = m.display_name
                out.append(f"{name} ({streak}일)")
            return out
        lines_checked = resolve(checked)
        lines_not = resolve(not_checked)
        desc = (
            f"🟢 오늘 출석 ({len(checked)}명)\n" + ("\n".join(lines_checked) if lines_checked else "(표시할 인원 없음)") +
            "\n\n🔴 미출석 (오늘 기준, 과거 출석자)\n" + ("\n".join(lines_not) if lines_not else "(표시할 인원 없음)")
        )
        embed = discord.Embed(title="📅 오늘의 출석 현황", description=desc, color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed)

    @group.command(name="최대연속", description="최대 연속 출석 일수 리더보드")
    @app_commands.describe(상위="표시 인원(기본 20)")
    async def max_streak_board(self, interaction: discord.Interaction, 상위: int = 20):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        topn = max(1, min(int(상위), 50))
        rows = db.attendance_max_streak_leaderboard(interaction.guild.id, topn * 2)
        if not rows:
            await interaction.response.send_message("아직 출석 기록이 없습니다.", ephemeral=True)
            return
        lines = []
        for uid, ms, td in rows:
            m = interaction.guild.get_member(uid)
            if not m:
                continue
            name = m.display_name
            lines.append(f"{name} — 최대 {ms}일 (총 {td}회)")
            if len(lines) >= topn:
                break
        embed = discord.Embed(title="🏆 최대 연속 출석", description="\n".join(lines), color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    cog = Attendance(bot)
    await bot.add_cog(cog)
    if not cog._notify_yday_not_today.is_running():
        cog._notify_yday_not_today.start()
