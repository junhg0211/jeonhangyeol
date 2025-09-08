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

    @staticmethod
    def _pick_rank(member: discord.Member, rank_names: list[str]) -> str | None:
        have = {r.name for r in member.roles}
        for rn in rank_names:
            if rn in have:
                return rn
        return None

    def _build_team_suffix(self, guild: discord.Guild, user_id: int, budget: int, rank: str | None) -> str:
        team_id = db.get_user_team_id(guild.id, user_id)
        names = db.get_team_path_names(guild.id, team_id) if team_id else []
        # 가장 하위 팀부터 거꾸로 누적, 길이 초과 시 상위는 생략
        suffix_tokens: list[str] = []
        # rank는 항상 맨 끝에 붙음
        rank_extra = (1 + len(rank)) if rank else 0  # 앞 공백 포함 길이
        remain = max(0, budget)
        # 나중에 공백으로 join되니, 각 token 사이 1칸을 고려하여 미리 관리
        # 최소 보장: 하위 팀 하나는 넣어보되, 그래도 안되면 빈 문자열 반환
        added_any = False
        for name in reversed(names):  # leaf -> root
            need = (1 if suffix_tokens else 0) + len(name)
            if need + (rank_extra if not added_any else 0) <= remain:
                suffix_tokens.insert(0, name)  # 앞쪽(루트쪽)으로 쌓기 위해 0에 삽입
                remain -= need
                added_any = True
            else:
                break
        # rank 부착 시도
        if rank:
            need = (1 if suffix_tokens else 0) + len(rank)
            if need <= remain:
                suffix_tokens.append(rank)
            else:
                # rank가 안 붙더라도 팀만 유지
                pass
        return " ".join(suffix_tokens)

    async def _apply_member_nick(self, member: discord.Member) -> bool:
        # 닉네임 길이 제한: 32자
        try:
            base = self._extract_base_name(member.display_name)
        except Exception:
            base = member.display_name
        max_len = 32
        # 권한 체크
        guild = member.guild
        me = guild.me  # type: ignore
        if not me or not guild.me.guild_permissions.manage_nicknames:
            return False
        if member.top_role >= me.top_role:
            return False

        rank_names = db.get_rank_roles(guild.id)
        rank = self._pick_rank(member, rank_names)

        # 접미사 예: "팀 팀2 직급" -> "base | 접미사"
        sep = " | "
        budget = max_len - len(base) - len(sep)
        if budget <= 0:
            # base가 너무 길면 수정하지 않음
            return False
        suffix = self._build_team_suffix(guild, member.id, budget, rank)
        if not suffix:
            target = base
        else:
            target = f"{base}{sep}{suffix}"
        if target == member.display_name:
            return False
        try:
            await member.edit(nick=target, reason="팀/직급 닉네임 반영")
            return True
        except Exception:
            return False

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
        # 닉네임 반영 시도
        changed = await self._apply_member_nick(대상)
        note = " (닉네임 반영됨)" if changed else ""
        # 비는 팀 정리(이전 팀부터 위로 올라가며 비어 있으면 삭제)
        pruned = 0
        try:
            pruned = db.prune_empty_upwards(interaction.guild.id, prev_team_id)
        except Exception:
            pass
        extra = f" — 빈 팀 {pruned}개 삭제" if pruned > 0 else ""
        await interaction.response.send_message(f"{대상.mention}님의 팀이 '{경로}'로 변경되었습니다.{note}{extra}", ephemeral=True)


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

    @group.command(name="정리", description="사람이 한 명도 없는 팀(하위 포함)을 일괄 삭제합니다.")
    @app_commands.default_permissions(manage_guild=True)
    async def prune_empty(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        # 깊은 팀부터 검사하며 비어 있으면 삭제
        rows = db.list_teams(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("등록된 팀이 없습니다.", ephemeral=True)
            return
        # build parent map and order by depth desc
        parents = {}
        for tid, name, parent in rows:
            parents[tid] = parent
        # compute depth from root
        depth = {}
        for tid, name, parent in rows:
            d = 0
            p = parent
            while p is not None:
                d += 1
                p = parents.get(p)
            depth[tid] = d
        deleted = 0
        # skip root by name
        for tid, name, parent in sorted(rows, key=lambda r: depth.get(r[0], 0), reverse=True):
            if name == db.TEAM_ROOT_NAME:
                continue
            try:
                if not db.team_subtree_has_members(interaction.guild.id, tid):
                    deleted += db.delete_team_subtree(interaction.guild.id, tid)
            except Exception:
                pass
        await interaction.response.send_message(f"정리 완료: 삭제된 팀 {deleted}개", ephemeral=True)

    @group.command(name="닉네임적용", description="팀/직급 정보를 닉네임에 반영합니다.")
    @app_commands.describe(대상="미지정 시 본인")
    async def apply_nick_cmd(self, interaction: discord.Interaction, 대상: discord.Member | None = None):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        member = 대상 or interaction.user  # type: ignore
        # 권한: 본인 허용, 타인은 관리자만
        is_self = member.id == interaction.user.id
        perms = getattr(interaction.user, "guild_permissions", None)
        if not is_self and not (perms and (perms.manage_guild or perms.administrator)):
            await interaction.response.send_message("다른 사용자의 닉네임 적용은 관리자만 가능합니다.", ephemeral=True)
            return
        changed = await self._apply_member_nick(member)
        msg = "닉네임을 갱신했습니다." if changed else "변경 사항이 없습니다(권한/길이 제한 가능)."
        await interaction.response.send_message(msg, ephemeral=True)

    @group.command(name="직급목록", description="닉네임에 반영할 직급 역할 목록을 확인합니다.")
    async def show_rank_list(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        ranks = db.get_rank_roles(interaction.guild.id)
        await interaction.response.send_message("현재 직급 목록: " + ", ".join(ranks), ephemeral=True)

    @group.command(name="직급목록설정", description="직급 역할 목록을 설정합니다(쉼표/공백 구분). 순서가 우선순위입니다.")
    @app_commands.default_permissions(manage_guild=True)
    async def set_rank_list(self, interaction: discord.Interaction, 목록: str):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        raw = [t for t in 목록.replace(",", " ").split() if t]
        if not raw:
            await interaction.response.send_message("최소 1개 이상의 직급을 입력해 주세요.", ephemeral=True)
            return
        db.set_rank_roles(interaction.guild.id, raw)
        await interaction.response.send_message("직급 목록을 업데이트했습니다: " + ", ".join(raw), ephemeral=True)

    @group.command(name="직급", description="대상의 직급 역할을 설정합니다(기존 직급 역할 해제).")
    @app_commands.describe(대상="직급을 변경할 사용자", 직급="역할 이름과 동일하게 입력")
    @app_commands.default_permissions(manage_roles=True)
    async def set_member_rank(self, interaction: discord.Interaction, 대상: discord.Member, 직급: str):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        ranks = db.get_rank_roles(interaction.guild.id)
        if 직급 not in ranks:
            await interaction.response.send_message("설정된 직급 목록에 없는 이름입니다. /팀 직급목록으로 확인하세요.", ephemeral=True)
            return
        # 역할 찾기
        role_to_add = discord.utils.get(interaction.guild.roles, name=직급)
        if not role_to_add:
            await interaction.response.send_message("해당 이름의 역할이 서버에 존재하지 않습니다.", ephemeral=True)
            return
        me = interaction.guild.me  # type: ignore
        if not me or role_to_add >= me.top_role or not me.guild_permissions.manage_roles:
            await interaction.response.send_message("역할 관리 권한이 부족합니다.", ephemeral=True)
            return
        # 기존 직급 역할 제거
        to_remove = [discord.utils.get(interaction.guild.roles, name=r) for r in ranks]
        to_remove = [r for r in to_remove if r and r in 대상.roles and r != role_to_add]
        try:
            if to_remove:
                await 대상.remove_roles(*to_remove, reason="직급 변경(봇)")
            if role_to_add not in 대상.roles:
                await 대상.add_roles(role_to_add, reason="직급 부여(봇)")
        except Exception:
            await interaction.response.send_message("역할 변경 중 오류가 발생했습니다.", ephemeral=True)
            return
        # 닉네임 반영
        await self._apply_member_nick(대상)
        await interaction.response.send_message(f"{대상.mention}님의 직급을 '{직급}'(으)로 설정했습니다.", ephemeral=True)

    # 역할 변경 시 닉네임 자동 반영
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        try:
            if before.guild.id != after.guild.id:
                return
        except Exception:
            return
        # 직급 역할 변화가 있는지 확인
        ranks = db.get_rank_roles(after.guild.id)
        names_before = {r.name for r in before.roles}
        names_after = {r.name for r in after.roles}
        touched = any(((r in names_before) != (r in names_after)) for r in ranks)
        if touched:
            await self._apply_member_nick(after)



async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))
