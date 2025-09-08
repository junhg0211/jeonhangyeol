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
            db.inv_team_set_user_path(interaction.guild.id, 대상.id, 경로)
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
        # Inventory-based: build from user paths
        uid_to_path = db.inv_team_all_user_paths(interaction.guild.id)
        if not uid_to_path:
            # Fallback migration from legacy tables if present
            try:
                migrated = db.inv_team_migrate_from_tables(interaction.guild.id)
                if migrated:
                    uid_to_path = db.inv_team_all_user_paths(interaction.guild.id)
            except Exception:
                pass
        if not uid_to_path:
            await interaction.followup.send("등록된 팀이 없습니다.", ephemeral=True)
            return
        # Build map: path -> [user_ids]
        path_members: dict[str, list[int]] = {}
        for uid, path in uid_to_path.items():
            path_members.setdefault(path, []).append(uid)
        # Build set of all node paths (prefixes)
        all_nodes: set[str] = set()
        for path in path_members.keys():
            tokens = path.split()
            for i in range(1, len(tokens) + 1):
                all_nodes.add(" ".join(tokens[:i]))
        # Compute subtree totals quickly
        def subtree_total(prefix: str) -> int:
            return sum(len(members) for p, members in path_members.items() if p == prefix or p.startswith(prefix + " "))

        # Order nodes by depth then lexicographically
        def depth_of(p: str) -> int:
            return 0 if p == db.TEAM_ROOT_NAME else len(p.split())
        ordered = sorted(all_nodes, key=lambda p: (len(p.split()), p))

        lines: list[str] = []
        for node in ordered:
            name = node.split()[-1]
            depth = len(node.split()) - 1
            # direct members list
            member_names: list[str] = []
            for uid in path_members.get(node, []):
                m = interaction.guild.get_member(uid)
                if not m:
                    continue
                try:
                    base = self._extract_base_name(m.display_name)
                except Exception:
                    base = m.display_name
                member_names.append(base)
            total_cnt = subtree_total(node)
            indent = "  " * depth
            if member_names:
                lines.append(f"{indent}• {name} — 총 {total_cnt}명: {', '.join(member_names)}")
            else:
                lines.append(f"{indent}• {name} — 총 {total_cnt}명")

        embed = discord.Embed(title="👥 팀 목록", description="\n".join(lines) if lines else "(표시할 팀이 없습니다)", color=discord.Color.purple())
        await interaction.followup.send(embed=embed)

    @group.command(name="삭제", description="지정한 팀과 하위 팀의 소속을 일괄 해제합니다.")
    @app_commands.describe(경로="예: 이정그룹 이정조주 술부")
    @app_commands.default_permissions(manage_guild=True)
    async def delete_team(self, interaction: discord.Interaction, 경로: str):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        tokens = [t for t in (경로 or "").split() if t]
        if not tokens:
            await interaction.response.send_message("팀 경로가 비어 있습니다.", ephemeral=True)
            return
        prefix = " ".join(tokens)
        uid_to_path = db.inv_team_all_user_paths(interaction.guild.id)
        targets = [uid for uid, p in uid_to_path.items() if p == prefix or p.startswith(prefix + " ")]
        if not targets:
            await interaction.response.send_message("해당 팀(및 하위 팀)에 소속된 인원이 없습니다.", ephemeral=True)
            return
        for uid in targets:
            try:
                db.inv_team_clear_user(interaction.guild.id, uid)
            except Exception:
                pass
        await interaction.response.send_message(f"삭제 완료: 소속 해제 {len(targets)}명 (팀 '{prefix}' 및 하위)", ephemeral=True)

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
        prev_path = db.inv_team_get_user_path(interaction.guild.id, member.id)
        if prev_path is None:
            await interaction.response.send_message("이미 팀에 소속되어 있지 않습니다.", ephemeral=True)
            return
        # 팀 소속 해제
        db.inv_team_clear_user(interaction.guild.id, member.id)
        # 닉네임 변경 기능 제거됨
        # 빈 팀 정리
        target_note = f" {member.mention}" if not is_self else ""
        await interaction.response.send_message(f"팀 소속을 해제했습니다.{target_note}", ephemeral=True)

    # (역할 변경 훅 제거)



async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))
