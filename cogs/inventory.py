import discord
from discord import app_commands
from discord.ext import commands

import database as db
import json
import asyncio
import time


class Inventory(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()
        # message_id -> pagination context
        self._pages: dict[int, dict] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        print("Inventory cog가 준비되었습니다.")

    # 1) 인벤토리 조회(+검색, 페이지네이션): /인벤토리 [유저] [검색]
    @app_commands.command(name="인벤토리", description="유저의 인벤토리를 확인합니다(검색/페이지 지원).")
    @app_commands.describe(유저="확인할 대상 (기본: 본인)", 검색="아이템 이름 또는 이모지 일부")
    async def inventory(self, interaction: discord.Interaction, 유저: discord.Member | None = None, 검색: str | None = None):
        target = 유저 or interaction.user
        rows = db.list_inventory(target.id, query=검색)

        # 페이지네이션 설정
        per_page = 10
        total = len(rows)
        total_pages = max(1, (total + per_page - 1) // per_page)

        def page_embed(page: int) -> discord.Embed:
            start = (page - 1) * per_page
            end = start + per_page
            page_rows = rows[start:end]
            if not page_rows:
                desc = "검색 결과가 없습니다." if 검색 else "가지고 있는 아이템이 없습니다."
            else:
                lines = [f"{emoji} {name} × **{qty}**" for (emoji, name, qty) in page_rows]
                desc = "\n".join(lines)
            title = f"🎒 {target.display_name}님의 인벤토리"
            if 검색:
                title += f" — 검색: {검색}"
            embed = discord.Embed(title=title, description=desc, color=discord.Color.blurple())
            embed.set_footer(text=f"페이지 {page}/{total_pages} • 반응으로 이동: ⬅️ ➡️ • 1분 후 만료")
            return embed

        await interaction.response.send_message(embed=page_embed(1))
        msg = await interaction.original_response()

        # 데이터가 없다면 페이지네이션 컨트롤 추가 생략
        if total == 0:
            return

        # 컨텍스트 저장
        self._pages[msg.id] = {
            "owner_id": interaction.user.id,
            "target_id": target.id,
            "rows": rows,
            "per_page": per_page,
            "page": 1,
            "total_pages": total_pages,
            "search": 검색 or "",
            "expires_at": time.monotonic() + 60,
        }

        # 반응 추가 (권한 없을 수 있어 예외 무시)
        for emoji in ("⬅️", "➡️"):
            try:
                await msg.add_reaction(emoji)
            except Exception:
                pass

        # 타임아웃 스케줄링 (1분)
        asyncio.create_task(self._schedule_expire(msg))

    # 2) 아이템 양도: /양도 받는사람 이모지 이름 [수량]
    @app_commands.command(name="양도", description="보유 아이템을 다른 사람에게 전달합니다.")
    @app_commands.describe(
        받는사람="아이템을 받을 대상",
        아이템="보유 아이템에서 선택 (자동완성)",
        수량="전달할 수량 (기본 1)"
    )
    async def give_item(
        self,
        interaction: discord.Interaction,
        받는사람: discord.Member,
        아이템: str,
        수량: int = 1,
    ):
        # 기본 검증
        if 수량 <= 0:
            await interaction.response.send_message("수량은 0보다 커야 합니다.", ephemeral=True)
            return
        if 받는사람.bot:
            await interaction.response.send_message("봇에게는 아이템을 줄 수 없습니다.", ephemeral=True)
            return
        if 받는사람.id == interaction.user.id:
            await interaction.response.send_message("자기 자신에게는 양도할 수 없습니다.", ephemeral=True)
            return

        # 아이템 자동완성 값 파싱(필수)
        try:
            data = json.loads(아이템)
            emo = str(data.get('e', '')).strip()
            name = str(data.get('n', '')).strip()
        except Exception:
            await interaction.response.send_message("아이템을 자동완성 목록에서 선택하세요.", ephemeral=True)
            return
        if not emo or not name:
            await interaction.response.send_message("잘못된 아이템입니다. 다시 선택하세요.", ephemeral=True)
            return
        # 투자 종목 아이템 차단(특허 아이템은 허용)
        try:
            if db.is_instrument_item_name(name):
                await interaction.response.send_message("투자 종목 아이템은 양도할 수 없습니다. /투자 명령을 이용해 주세요.", ephemeral=True)
                return
        except Exception:
            pass

        await interaction.response.defer()

        # 특허 아이템은 특허 소유권도 함께 이전
        if db.is_patent_item_name(name):
            if 수량 != 1:
                await interaction.followup.send("특허 아이템은 1개 단위로만 이전할 수 있습니다.")
                return
            word = name.split(":", 1)[1] if ":" in name else name
            ok = db.transfer_patent(interaction.guild.id, interaction.user.id, 받는사람.id, word)
            if not ok:
                await interaction.followup.send("특허 소유권 이전에 실패했습니다(소유자 아님).")
                return
        try:
            sender_qty, receiver_qty = db.transfer_item(
                sender_id=interaction.user.id,
                receiver_id=받는사람.id,
                name=name,
                emoji=emo,
                qty=수량,
            )
        except ValueError as e:
            await interaction.followup.send(str(e))
            return

        embed = discord.Embed(
            title="🎁 아이템 양도 완료",
            description=(
                f"{interaction.user.mention}님이 {받는사람.mention}님에게\n"
                f"{emo} {name} × **{수량}** 을(를) 전달했습니다."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"보유수량: 보낸사람 {sender_qty}개 / 받은사람 {receiver_qty}개")
        await interaction.followup.send(embed=embed)

    # 4) 아이템 폐기: /폐기 이모지 이름 [수량]
    @app_commands.command(name="폐기", description="인벤토리에서 아이템을 버립니다.")
    @app_commands.describe(
        아이템="보유 아이템에서 선택 (자동완성)",
        수량="버릴 수량 (기본 1)"
    )
    async def discard(
        self,
        interaction: discord.Interaction,
        아이템: str,
        수량: int = 1,
    ):
        if 수량 <= 0:
            await interaction.response.send_message("수량은 0보다 커야 합니다.", ephemeral=True)
            return

        # 아이템 자동완성 값 파싱(필수)
        try:
            data = json.loads(아이템)
            emo = str(data.get('e', '')).strip()
            name = str(data.get('n', '')).strip()
        except Exception:
            await interaction.response.send_message("아이템을 자동완성 목록에서 선택하세요.", ephemeral=True)
            return
        if not emo or not name:
            await interaction.response.send_message("잘못된 아이템입니다. 다시 선택하세요.", ephemeral=True)
            return
        # 투자 종목 아이템 차단(특허 아이템은 허용)
        try:
            if db.is_instrument_item_name(name):
                await interaction.response.send_message("투자 종목 아이템은 폐기할 수 없습니다. /투자 매도로 정리해 주세요.", ephemeral=True)
                return
        except Exception:
            pass

        await interaction.response.defer(ephemeral=True)

        # 특허 아이템은 취소 처리 동기화
        if db.is_patent_item_name(name):
            if 수량 != 1:
                await interaction.followup.send("특허 아이템은 1개 단위로만 폐기(취소)할 수 있습니다.", ephemeral=True)
                return
            word = name.split(":", 1)[1] if ":" in name else name
            ok = db.cancel_patent(interaction.guild.id, interaction.user.id, word)
            if not ok:
                await interaction.followup.send("해당 특허가 없거나 취소할 수 없습니다.", ephemeral=True)
                return
            # 남은 수량 조회
            rem = 0
            for e, n, q in db.list_inventory(interaction.user.id):
                if n == name and e == emo:
                    rem = q
                    break
            embed = discord.Embed(
                title="📜 특허 취소 완료",
                description=f"{emo} {name} × 1 특허가 취소되었습니다.",
                color=discord.Color.red(),
            )
            embed.set_footer(text=f"현재 보유 수량: {rem}개")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        try:
            remaining = db.discard_item(interaction.user.id, name, emo, 수량)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        embed = discord.Embed(
            title="🗑️ 아이템 폐기 완료",
            description=f"{emo} {name} × **{수량}** 을(를) 버렸습니다.",
            color=discord.Color.red(),
        )
        embed.set_footer(text=f"현재 보유 수량: {remaining}개")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # --------- 자동완성: 보유 아이템 후보 제공 ---------
    @give_item.autocomplete("아이템")
    async def _autocomplete_give_item(self, interaction: discord.Interaction, current: str):
        user_id = interaction.user.id
        rows = db.list_inventory(user_id, query=current or None)
        # 최대 25개 제한
        choices = []
        for (emoji, name, qty) in rows[:25]:
            # 투자 종목 아이템만 숨김
            try:
                if db.is_instrument_item_name(name):
                    continue
            except Exception:
                pass
            label = f"{emoji} {name} × {qty}"
            value = json.dumps({"e": emoji, "n": name}, ensure_ascii=False)
            choices.append(app_commands.Choice(name=label, value=value))
        return choices

    @discard.autocomplete("아이템")
    async def _autocomplete_discard_item(self, interaction: discord.Interaction, current: str):
        user_id = interaction.user.id
        rows = db.list_inventory(user_id, query=current or None)
        choices = []
        for (emoji, name, qty) in rows[:25]:
            # 투자 종목 아이템만 숨김
            try:
                if db.is_instrument_item_name(name):
                    continue
            except Exception:
                pass
            label = f"{emoji} {name} × {qty}"
            value = json.dumps({"e": emoji, "n": name}, ensure_ascii=False)
            choices.append(app_commands.Choice(name=label, value=value))
        return choices

    # 3) 테스트/운영용 아이템 지급: /지급 대상 이모지 이름 [수량]
    @app_commands.command(name="지급", description="관리자 전용: 특정 유저에게 아이템을 지급합니다.")
    @app_commands.describe(
        대상="아이템을 받을 대상",
        이모지="아이템 이모지",
        이름="아이템 이름",
        수량="지급할 수량 (기본 1)"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def grant(
        self,
        interaction: discord.Interaction,
        대상: discord.Member,
        이모지: str,
        이름: str,
        수량: int = 1,
    ):
        # 권한 및 기본 검증
        if not interaction.guild:
            await interaction.response.send_message("서버 내에서만 사용할 수 있습니다.", ephemeral=True)
            return
        perms = getattr(interaction.user, "guild_permissions", None)
        if not (perms and (perms.manage_guild or perms.administrator)):
            await interaction.response.send_message("이 명령을 사용할 권한이 없습니다.", ephemeral=True)
            return
        if 수량 <= 0:
            await interaction.response.send_message("수량은 0보다 커야 합니다.", ephemeral=True)
            return
        if 대상.bot:
            await interaction.response.send_message("봇에게는 지급할 수 없습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            new_qty = db.grant_item(대상.id, 이름, 이모지, 수량)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ 아이템 지급 완료",
            description=(
                f"{대상.mention}에게 {이모지} {이름} × **{수량}** 지급되었습니다.\n"
                f"현재 보유 수량: **{new_qty}개**"
            ),
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # 반응 기반 페이지네이션 처리
    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot:
            return
        msg = reaction.message
        ctx = self._pages.get(msg.id)
        if not ctx:
            return
        # 소유자만 조작 가능
        if user.id != ctx["owner_id"]:
            return

        emoji = str(reaction.emoji)
        page = ctx["page"]
        total_pages = ctx["total_pages"]

        # 만료 확인
        if time.monotonic() > ctx.get("expires_at", 0):
            # 만료된 경우 컨트롤 제거 시도 후 컨텍스트 삭제
            try:
                await msg.clear_reactions()
            except Exception:
                pass
            self._pages.pop(msg.id, None)
            return

        if emoji == "⬅️" and page > 1:
            page -= 1
        elif emoji == "➡️" and page < total_pages:
            page += 1
        else:
            # 무효 입력
            return

        # 페이지 갱신
        ctx["page"] = page
        rows = ctx["rows"]
        per_page = ctx["per_page"]
        search = ctx["search"]

        start = (page - 1) * per_page
        end = start + per_page
        page_rows = rows[start:end]
        if not page_rows:
            desc = "검색 결과가 없습니다." if search else "가지고 있는 아이템이 없습니다."
        else:
            desc = "\n".join([f"{e} {n} × **{q}**" for (e, n, q) in page_rows])
        title = f"🎒 {(msg.guild.get_member(ctx['target_id']).display_name if msg.guild else '유저')}님의 인벤토리"
        if search:
            title += f" — 검색: {search}"
        embed = discord.Embed(title=title, description=desc, color=discord.Color.blurple())
        embed.set_footer(text=f"페이지 {page}/{total_pages} • 반응으로 이동: ⬅️ ➡️ • 1분 후 만료")
        try:
            await msg.edit(embed=embed)
        except Exception:
            pass
        # 사용자 반응 제거 시도(권한 없으면 무시)
        try:
            await msg.remove_reaction(reaction.emoji, user)
        except Exception:
            pass

    async def _schedule_expire(self, msg: discord.Message):
        # 1분 대기 후 컨트롤 비활성화
        await asyncio.sleep(60)
        ctx = self._pages.get(msg.id)
        if not ctx:
            return
        # 임베드 업데이트(만료 표시)
        rows = ctx["rows"]
        per_page = ctx["per_page"]
        page = ctx["page"]
        total_pages = ctx["total_pages"]
        search = ctx["search"]
        start = (page - 1) * per_page
        end = start + per_page
        page_rows = rows[start:end]
        if not page_rows:
            desc = "검색 결과가 없습니다." if search else "가지고 있는 아이템이 없습니다."
        else:
            desc = "\n".join([f"{e} {n} × **{q}**" for (e, n, q) in page_rows])
        try:
            target_member = msg.guild.get_member(ctx['target_id']) if msg.guild else None
            title = f"🎒 {(target_member.display_name if target_member else '유저')}님의 인벤토리"
            if search:
                title += f" — 검색: {search}"
            embed = discord.Embed(title=title, description=desc, color=discord.Color.blurple())
            embed.set_footer(text=f"페이지 {page}/{total_pages} • 만료됨")
            await msg.edit(embed=embed)
        except Exception:
            pass
        # 컨트롤 제거 및 컨텍스트 삭제
        try:
            await msg.clear_reactions()
        except Exception:
            pass
        self._pages.pop(msg.id, None)


async def setup(bot: commands.Bot):
    await bot.add_cog(Inventory(bot))
