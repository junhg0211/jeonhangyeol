import discord
from discord.ext import commands
from discord import app_commands

import database as db


class Teams(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()

    group = app_commands.Group(name="팀", description="팀 관리")

    # ---------- 내부 유틸 ----------
    @staticmethod
    def _extract_base_name(display_name: str) -> str:
        # 봇이 붙인 접미사 패턴: "기존닉 | 팀 경로 [직급]"
        # 기존 포맷이 없으면 전체를 기본 닉네임으로 사용
        parts = display_name.split(" | ", 1)
        return parts[0]

    # (닉네임/직급 관련 로직 제거)

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
        # 이전 팀 저장(이동 후 비는 팀 정리용)
        prev_team_id = None
        try:
            prev_team_id = db.get_user_team_id(interaction.guild.id, 대상.id)
        except Exception:
            pass
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
        # Defer because building the tree can take time
        try:
            await interaction.response.defer(thinking=True)
        except Exception:
            pass
        # DB-based: build from teams table
        rows = db.list_teams(interaction.guild.id)
        if not rows:
            await interaction.followup.send("등록된 팀이 없습니다.", ephemeral=True)
            return
        by_parent: dict[int | None, list[tuple[int, str]]] = {}
        id_to_name: dict[int, str] = {}
        for tid, name, parent in rows:
            by_parent.setdefault(parent, []).append((tid, name))
            id_to_name[tid] = name
        # find root
        root_id = None
        for tid, name, parent in rows:
            if parent is None and name == db.TEAM_ROOT_NAME:
                root_id = tid
                break
        lines: list[str] = []
        def dfs(tid: int, name: str, depth: int):
            if name != db.TEAM_ROOT_NAME:
                members = db.list_team_members(interaction.guild.id, tid)
                total_cnt = db.count_team_subtree_members(interaction.guild.id, tid)
                children = by_parent.get(tid, [])
                # skip showing nodes that are completely empty and have no children
                if total_cnt == 0 and not children:
                    return
                member_names: list[str] = []
                for uid in members:
                    m = interaction.guild.get_member(uid)
                    if m:
                        try:
                            base = self._extract_base_name(m.display_name)
                        except Exception:
                            base = m.display_name
                        member_names.append(base)
                indent = "  " * depth
                if member_names:
                    lines.append(f"{indent}• {name} — 총 {total_cnt}명: {', '.join(member_names)}")
                else:
                    lines.append(f"{indent}• {name} — 총 {total_cnt}명")
            for child_id, child_name in by_parent.get(tid, []):
                dfs(child_id, child_name, depth + (0 if name == db.TEAM_ROOT_NAME else 1))
        if root_id is not None:
            dfs(root_id, db.TEAM_ROOT_NAME, 0)
        else:
            for tid, name in by_parent.get(None, []):
                dfs(tid, name, 0)

        embed = discord.Embed(title="👥 팀 목록", description="\n".join(lines) if lines else "(표시할 팀이 없습니다)", color=discord.Color.purple())
        await interaction.followup.send(embed=embed)

    @group.command(name="삭제", description="지정한 팀과 하위 팀의 소속을 일괄 해제합니다.")
    @app_commands.describe(경로="예: 이정그룹 이정조주 술부")
    @app_commands.default_permissions(manage_guild=True)
    async def delete_team(self, interaction: discord.Interaction, 경로: str):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        # Defer early to avoid 3s timeout while processing
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)
        except Exception:
            pass
        tokens = [t for t in (경로 or "").split() if t]
        if not tokens:
            await interaction.followup.send("팀 경로가 비어 있습니다.", ephemeral=True)
            return
        path_norm = " ".join(tokens)
        team_id = db.find_team_by_path(interaction.guild.id, path_norm)
        if team_id is None:
            await interaction.followup.send("해당 경로의 팀이 존재하지 않습니다.", ephemeral=True)
            return
        cleared = db.clear_membership_subtree(interaction.guild.id, team_id)
        # 팀/하위 팀에 더 이상 인원이 없다면 팀 노드도 삭제
        removed = 0
        parent_for_prune = db.get_team_parent(interaction.guild.id, team_id)
        try:
            if not db.team_subtree_has_members(interaction.guild.id, team_id):
                removed = db.delete_team_subtree(interaction.guild.id, team_id)
                # 상위 빈 팀도 정리
                removed += db.delete_empty_ancestors(interaction.guild.id, team_id)
        except Exception:
            pass
        extra = f", 팀 노드 {removed}개 삭제" if removed > 0 else ""
        await interaction.followup.send(f"삭제 완료: 소속 해제 {cleared}명 (팀 '{path_norm}' 및 하위){extra}", ephemeral=True)

    # (직급 관련 명령 제거)

    @group.command(name="나가기", description="팀 소속을 해제합니다(관리자는 대상 지정 가능).")
    @app_commands.describe(대상="미지정 시 본인")
    async def leave_team(self, interaction: discord.Interaction, 대상: discord.Member | None = None):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        member = 대상 or interaction.user  # type: ignore
        # 권한: 본인 허용, 타인은 관리자만
        is_self = member.id == interaction.user.id
        perms = getattr(interaction.user, "guild_permissions", None)
        if not is_self and not (perms and (perms.manage_guild or perms.administrator)):
            await interaction.response.send_message("다른 사용자의 팀 나가기는 관리자만 가능합니다.", ephemeral=True)
            return
        prev_team_id = db.get_user_team_id(interaction.guild.id, member.id)
        if prev_team_id is None:
            await interaction.response.send_message("이미 팀에 소속되어 있지 않습니다.", ephemeral=True)
            return
        # 팀 소속 해제 (DB)
        db.clear_user_team(interaction.guild.id, member.id)
        # 닉네임 변경 기능 제거됨
        # 빈 팀 정리
        target_note = f" {member.mention}" if not is_self else ""
        await interaction.response.send_message(f"팀 소속을 해제했습니다.{target_note}", ephemeral=True)

    # (역할 변경 훅 제거)



async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))
