import discord
from discord.ext import commands
from discord import app_commands

import db


class Attendance(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()

    group = app_commands.Group(name="출석", description="출석 체크 및 랭킹")

    @group.command(name="하기", description="오늘 출석하고 보상을 받습니다.")
    async def check_in(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        already, streak, reward, maxs = db.attendance_check_in(interaction.guild.id, interaction.user.id)
        if already:
            await interaction.response.send_message(f"오늘은 이미 출석했습니다. 현재 연속 {streak}일!", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ 출석 완료! 연속 {streak}일, 보상 {reward}원 지급(최대 연속 {maxs}일)", ephemeral=True
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
    await bot.add_cog(Attendance(bot))
