import discord
from discord.ext import commands
from discord import app_commands

import db


class Teams(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()

    group = app_commands.Group(name="팀", description="팀 관리")

    @group.command(name="변경", description="사용자의 팀을 변경합니다. 경로는 공백으로 상하위 구분")
    @app_commands.describe(대상="팀을 변경할 사용자", 경로="예: 이정그룹 이정조주 술부")
    async def change_team(self, interaction: discord.Interaction, 대상: discord.Member, 경로: str):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        # 권한: 본인 변경은 허용, 타인 변경은 관리 권한 필요
        is_self = 대상.id == interaction.user.id
        perms = getattr(interaction.user, "guild_permissions", None)
        if not is_self and not (perms and (perms.manage_guild or perms.administrator)):
            await interaction.response.send_message("다른 사용자의 팀 변경은 관리자만 가능합니다.", ephemeral=True)
            return
        try:
            team_id = db.ensure_team_path(interaction.guild.id, 경로)
            db.set_user_team(interaction.guild.id, 대상.id, team_id)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message(f"{대상.mention}님의 팀이 '{경로}'로 변경되었습니다.", ephemeral=True)

    @group.command(name="목록", description="팀별 인원 목록을 표시합니다.")
    async def list_teams(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        rows = db.list_teams(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("등록된 팀이 없습니다.", ephemeral=True)
            return
        # Build tree
        by_parent = {}
        for tid, name, parent in rows:
            by_parent.setdefault(parent, []).append((tid, name))
        # root id
        root_id = None
        for tid, name, parent in rows:
            if parent is None and name == db.TEAM_ROOT_NAME:
                root_id = tid
                break

        lines = []
        def dfs(tid: int, name: str, depth: int):
            if name != db.TEAM_ROOT_NAME:
                members = db.list_team_members(interaction.guild.id, tid)
                member_names = []
                for uid in members:
                    m = interaction.guild.get_member(uid)
                    if m:
                        member_names.append(m.display_name)
                indent = "  " * depth
                if member_names:
                    lines.append(f"{indent}• {name} — {len(member_names)}명: {', '.join(member_names)}")
                else:
                    lines.append(f"{indent}• {name} — 0명")
            for child_id, child_name in by_parent.get(tid, []):
                dfs(child_id, child_name, depth + (0 if name == db.TEAM_ROOT_NAME else 1))

        if root_id is not None:
            dfs(root_id, db.TEAM_ROOT_NAME, 0)
        else:
            # no explicit root, show all
            for tid, name in by_parent.get(None, []):
                dfs(tid, name, 0)

        embed = discord.Embed(title="👥 팀 목록", description="\n".join(lines) if lines else "(표시할 팀이 없습니다)", color=discord.Color.purple())
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))

