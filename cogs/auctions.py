import discord
from discord.ext import commands, tasks
from discord import app_commands

import db
import json
import time
import asyncio


class Auctions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()
        self._pages: dict[int, dict] = {}
        # background closer
        self.closer.start()

    def cog_unload(self):
        try:
            self.closer.cancel()
        except Exception:
            pass

    auctions = app_commands.Group(name="경매", description="경매 기능")

    # 알림 채널 설정: /경매 채널 [채널]
    @auctions.command(name="채널", description="경매 시작 알림 채널을 설정/해제합니다.")
    @app_commands.describe(채널="경매 알림을 보낼 텍스트 채널 (비우면 해제)")
    @app_commands.default_permissions(manage_guild=True)
    async def set_channel(self, interaction: discord.Interaction, 채널: discord.TextChannel | None = None):
        if not interaction.guild:
            await interaction.response.send_message("서버 내에서만 사용할 수 있습니다.", ephemeral=True)
            return
        try:
            db.set_auction_channel(interaction.guild.id, 채널.id if 채널 else None)
        except Exception as e:
            await interaction.response.send_message(f"설정 중 오류: {e}", ephemeral=True)
            return
        if 채널:
            await interaction.response.send_message(f"경매 알림 채널을 {채널.mention}(으)로 설정했습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("경매 알림 채널 설정을 해제했습니다.", ephemeral=True)

    # 출품: /경매 출품 아이템 수량 시작가 기간(시간)
    @auctions.command(name="출품", description="보유 아이템을 경매에 출품합니다.")
    @app_commands.describe(
        아이템="보유 아이템에서 선택 (자동완성)",
        수량="출품 수량",
        시작가="시작가(최저가격)",
        기간시간="마감까지 시간 (1~720시간)",
    )
    async def list_item(
        self,
        interaction: discord.Interaction,
        아이템: str,
        수량: int,
        시작가: int,
        기간시간: int,
    ):
        if 수량 <= 0 or 시작가 < 0:
            await interaction.response.send_message("수량은 1이상, 시작가는 0 이상이어야 합니다.", ephemeral=True)
            return
        if 기간시간 < 1 or 기간시간 > 24 * 30:
            await interaction.response.send_message("기간은 1시간 이상 720시간(30일) 이하여야 합니다.", ephemeral=True)
            return
        try:
            data = json.loads(아이템)
            emoji = str(data.get("e", "")).strip()
            name = str(data.get("n", "")).strip()
        except Exception:
            await interaction.response.send_message("아이템을 자동완성에서 선택하세요.", ephemeral=True)
            return
        if not emoji or not name:
            await interaction.response.send_message("잘못된 아이템입니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            auction_id = db.create_auction(
                seller_id=interaction.user.id,
                name=name,
                emoji=emoji,
                qty=수량,
                start_price=시작가,
                duration_seconds=기간시간 * 3600,
                guild_id=interaction.guild.id if interaction.guild else None,
            )
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        embed = discord.Embed(
            title="📦 경매 출품 완료",
            description=(
                f"경매 ID: `{auction_id}`\n"
                f"아이템: {emoji} {name} × **{수량}**\n"
                f"시작가: **{시작가:,}원** • 마감: <t:{int(time.time()) + 기간시간*3600}:R>"
            ),
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        # 설정된 채널로 새 경매 알림 전송
        if interaction.guild:
            ch_id = db.get_auction_channel(interaction.guild.id)
            if ch_id:
                ch = self.bot.get_channel(ch_id)
                if isinstance(ch, (discord.TextChannel, discord.Thread)):
                    try:
                        notify = discord.Embed(
                            title="🛎️ 새 경매 시작",
                            description=(
                                f"경매 ID: `{auction_id}`\n"
                                f"아이템: {emoji} {name} × **{수량}**\n"
                                f"시작가: **{시작가:,}원**\n"
                                f"마감: <t:{int(time.time()) + 기간시간*3600}:R>"
                            ),
                            color=discord.Color.orange(),
                        )
                        seller = interaction.guild.get_member(interaction.user.id)
                        if seller:
                            notify.set_footer(text=f"출품자: {seller.display_name}")
                        await ch.send(embed=notify)
                    except Exception:
                        pass

    # 입찰: /경매 입찰 경매ID 금액
    @auctions.command(name="입찰", description="경매에 입찰합니다(선결제, 자동 환불).")
    @app_commands.describe(경매id="입찰할 경매 ID", 금액="입찰 금액")
    async def bid(self, interaction: discord.Interaction, 경매id: int, 금액: int):
        if 금액 <= 0:
            await interaction.response.send_message("입찰 금액은 0보다 커야 합니다.", ephemeral=True)
            return
        # 서버 일치 검사
        try:
            gid, end_at, status = db.get_auction_guild(경매id)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        if gid is not None and interaction.guild and interaction.guild.id != gid:
            await interaction.response.send_message("이 경매는 현재 서버의 경매가 아닙니다.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            new_bid, top_bidder = db.place_bid(경매id, interaction.user.id, 금액)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        embed = discord.Embed(
            title="📝 입찰 성공",
            description=f"경매 `{경매id}`에 **{new_bid:,}원**으로 입찰했습니다.",
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # 목록: /경매 목록 [검색]
    @auctions.command(name="목록", description="현재 진행중인 경매 목록을 확인합니다.")
    @app_commands.describe(검색="아이템 이름/이모지 일부", 페이지크기="페이지당 표시(기본 10, 최대 25)")
    async def list_auctions(self, interaction: discord.Interaction, 검색: str | None = None, 페이지크기: int = 10):
        per_page = max(1, min(int(페이지크기), 25))
        gid = interaction.guild.id if interaction.guild else None
        total = db.count_open_auctions(검색 or None, guild_id=gid)
        total_pages = max(1, (total + per_page - 1) // per_page)

        def build_embed(page: int) -> discord.Embed:
            offset = (page - 1) * per_page
            rows = db.list_open_auctions(offset, per_page, 검색 or None, guild_id=gid)
            lines = []
            for (aid, seller_id, name, emoji, qty, start_price, current_bid, current_bidder_id, end_at) in rows:
                price = current_bid if current_bid is not None else start_price
                seller = interaction.guild.get_member(seller_id) if interaction.guild else None
                seller_name = seller.display_name if seller else f"<@{seller_id}>"
                lines.append(
                    f"`#{aid}` {emoji} {name} ×{qty} — 현재가 **{price:,}원** — 판매자 {seller_name} — 마감 <t:{end_at}:R>"
                )
            desc = "\n".join(lines) if lines else "진행중인 경매가 없습니다."
            embed = discord.Embed(title="🏷️ 진행중인 경매", description=desc, color=discord.Color.blurple())
            embed.set_footer(text=f"페이지 {page}/{total_pages} • ⬅️ ➡️ • 1분 후 만료")
            return embed

        await interaction.response.send_message(embed=build_embed(1))
        msg = await interaction.original_response()
        if total == 0:
            return

        self._pages[msg.id] = {
            "owner_id": interaction.user.id,
            "per_page": per_page,
            "page": 1,
            "total_pages": total_pages,
            "search": 검색 or "",
            "expires_at": time.monotonic() + 60,
            "guild_id": gid,
        }
        for emoji in ("⬅️", "➡️"):
            try:
                await msg.add_reaction(emoji)
            except Exception:
                pass
        asyncio.create_task(self._expire_list(msg))

    @list_item.autocomplete("아이템")
    async def _ac_item(self, interaction: discord.Interaction, current: str):
        rows = db.list_inventory(interaction.user.id, query=current or None)
        choices = []
        for (emoji, name, qty) in rows[:25]:
            choices.append(app_commands.Choice(name=f"{emoji} {name} × {qty}", value=json.dumps({"e": emoji, "n": name}, ensure_ascii=False)))
        return choices

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot:
            return
        msg = reaction.message
        ctx = self._pages.get(msg.id)
        if not ctx or user.id != ctx["owner_id"]:
            return
        if time.monotonic() > ctx.get("expires_at", 0):
            try:
                await msg.clear_reactions()
            except Exception:
                pass
            self._pages.pop(msg.id, None)
            return
        emoji = str(reaction.emoji)
        page = ctx["page"]
        total_pages = ctx["total_pages"]
        per_page = ctx["per_page"]
        search = ctx["search"]
        if emoji == "⬅️" and page > 1:
            page -= 1
        elif emoji == "➡️" and page < total_pages:
            page += 1
        else:
            return
        ctx["page"] = page
        # rebuild
        offset = (page - 1) * per_page
        rows = db.list_open_auctions(offset, per_page, search or None, guild_id=ctx.get("guild_id"))
        lines = []
        for (aid, seller_id, name, emoji, qty, start_price, current_bid, current_bidder_id, end_at) in rows:
            price = current_bid if current_bid is not None else start_price
            seller = msg.guild.get_member(seller_id) if msg.guild else None
            seller_name = seller.display_name if seller else f"<@{seller_id}>"
            lines.append(
                f"`#{aid}` {emoji} {name} ×{qty} — 현재가 **{price:,}원** — 판매자 {seller_name} — 마감 <t:{end_at}:R>"
            )
        desc = "\n".join(lines) if lines else "진행중인 경매가 없습니다."
        embed = discord.Embed(title="🏷️ 진행중인 경매", description=desc, color=discord.Color.blurple())
        total = db.count_open_auctions(search or None, guild_id=ctx.get("guild_id"))
        total_pages = max(1, (total + per_page - 1) // per_page)
        ctx["total_pages"] = total_pages
        embed.set_footer(text=f"페이지 {page}/{total_pages} • ⬅️ ➡️ • 1분 후 만료")
        try:
            await msg.edit(embed=embed)
        except Exception:
            pass
        try:
            await msg.remove_reaction(reaction.emoji, user)
        except Exception:
            pass

    async def _expire_list(self, msg: discord.Message):
        await asyncio.sleep(60)
        ctx = self._pages.get(msg.id)
        if not ctx:
            return
        page = ctx["page"]
        per_page = ctx["per_page"]
        search = ctx["search"]
        offset = (page - 1) * per_page
        rows = db.list_open_auctions(offset, per_page, search or None, guild_id=ctx.get("guild_id"))
        lines = []
        for (aid, seller_id, name, emoji, qty, start_price, current_bid, current_bidder_id, end_at) in rows:
            price = current_bid if current_bid is not None else start_price
            seller = msg.guild.get_member(seller_id) if msg.guild else None
            seller_name = seller.display_name if seller else f"<@{seller_id}>"
            lines.append(
                f"`#{aid}` {emoji} {name} ×{qty} — 현재가 **{price:,}원** — 판매자 {seller_name} — 마감 <t:{end_at}:R>"
            )
        desc = "\n".join(lines) if lines else "진행중인 경매가 없습니다."
        embed = discord.Embed(title="🏷️ 진행중인 경매", description=desc, color=discord.Color.blurple())
        total = db.count_open_auctions(search or None, guild_id=ctx.get("guild_id"))
        total_pages = max(1, (total + per_page - 1) // per_page)
        embed.set_footer(text=f"페이지 {page}/{total_pages} • 만료됨")
        try:
            await msg.edit(embed=embed)
        except Exception:
            pass
        try:
            await msg.clear_reactions()
        except Exception:
            pass
        self._pages.pop(msg.id, None)

    # background finalizer
    @tasks.loop(seconds=30)
    async def closer(self):
        try:
            # 1) 서버에서 판매자가 없는 유찰 경매 파기
            due = db.list_due_unsold_auctions(50)
            discarded = 0
            for (aid, guild_id, seller_id, name, emoji, qty) in due:
                guild = self.bot.get_guild(guild_id) if guild_id else None
                seller_present = bool(guild and guild.get_member(seller_id))
                if not seller_present:
                    try:
                        db.discard_unsold_auction(aid)
                        discarded += 1
                    except Exception:
                        pass

            # 2) 나머지 경매 일반 규칙으로 정산(낙찰/유찰 반납)
            closed = db.finalize_due_auctions(50)
            if discarded or closed:
                print(f"[auctions] finalized={closed} discarded={discarded}")
        except Exception as e:
            print(f"[auctions] closer error: {e}")

    @closer.before_loop
    async def before_closer(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Auctions(bot))
