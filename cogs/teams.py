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

    def _subtree_has_active_members(self, guild: discord.Guild, root_tid: int, by_parent: dict[int | None, list[tuple[int, str]]]) -> bool:
        to_visit = [root_tid]
        while to_visit:
            cur = to_visit.pop()
            # direct members filtered by actual guild presence
            try:
                for uid in db.list_team_members(guild.id, cur):
                    if guild.get_member(uid):
                        return True
            except Exception:
                pass
            for child_id, _ in by_parent.get(cur, []) or []:
                to_visit.append(child_id)
        return False

    def _build_team_suffix(self, guild: discord.Guild, user_id: int, budget: int, rank: str | None) -> str:
        path = db.inv_team_get_user_path(guild.id, user_id)
        names = ([t for t in path.split() if t] if path else [])
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
        # rank 부착 시도(팀명이 하나 이상 있을 때만)
        if rank and suffix_tokens:
            need = (1 if suffix_tokens else 0) + len(rank)
            if need <= remain:
                suffix_tokens.append(rank)
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
            db.inv_team_set_user_path(interaction.guild.id, 대상.id, 경로)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        # 닉네임 반영 시도
        changed = await self._apply_member_nick(대상)
        note = " (닉네임 반영됨)" if changed else ""
        await interaction.response.send_message(f"{대상.mention}님의 팀이 '{경로}'로 변경되었습니다.{note}", ephemeral=True)


    @group.command(name="목록", description="팀별 인원 목록을 표시합니다.")
    async def list_teams(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        # Inventory-based: build from user paths
        uid_to_path = db.inv_team_all_user_paths(interaction.guild.id)
        if not uid_to_path:
            await interaction.response.send_message("등록된 팀이 없습니다.", ephemeral=True)
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
        await interaction.response.send_message(embed=embed)

    @group.command(name="정리", description="인벤토리 기반에서는 삭제할 팀 기록이 없습니다.")
    @app_commands.default_permissions(manage_guild=True)
    async def prune_empty(self, interaction: discord.Interaction):
        await interaction.response.send_message("인벤토리 기반 모드에서는 팀 레코드가 없어 정리할 항목이 없습니다.", ephemeral=True)

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
        # 닉네임 원복(접미사 제거)
        try:
            # 강제로 base만 남기기 위해 접미사가 없는 형태로 변경
            base = self._extract_base_name(member.display_name)
            me = interaction.guild.me  # type: ignore
            if me and me.guild_permissions.manage_nicknames and member.top_role < me.top_role:
                await member.edit(nick=base, reason="팀 나가기(봇)")
        except Exception:
            pass
        # 빈 팀 정리
        target_note = f" {member.mention}" if not is_self else ""
        await interaction.response.send_message(f"팀 소속을 해제했습니다.{target_note}", ephemeral=True)

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
