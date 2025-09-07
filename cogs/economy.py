# cogs/economy.py

import discord
from discord import app_commands  # app_commands를 import 합니다.
from discord.ext import commands
import db
import asyncio
import time

class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Ensure DB is ready on cog init
        db.init_db()
        # message_id -> pagination context for ranking
        self._rank_pages: dict[int, dict] = {}

    # 앱 커맨드는 Cog에 정의되면 자동으로 트리에 등록됩니다.

    def get_balance(self, user_id: int) -> int:
        return db.get_balance(user_id)

    @commands.Cog.listener()
    async def on_ready(self):
        print("Economy cog가 준비되었습니다.")

    # "돈" 그룹 명령어 정의
    money = app_commands.Group(name="돈", description="돈 관련 명령어")

    # 1-a. 소지금 확인: /돈 확인
    @money.command(name="확인", description="자신의 소지금을 확인합니다.")
    async def money_check(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        balance = self.get_balance(user_id)

        embed = discord.Embed(
            title=f"{interaction.user.display_name}님의 지갑",
            description=f"💰 현재 소지금: **{balance:,}원**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)

    # 2. 송금 슬래시 명령어 (기존 그대로 유지)
    @app_commands.command(name="송금", description="다른 사람에게 돈을 보냅니다.")
    @app_commands.describe(
        받는사람="돈을 보낼 대상을 선택하세요.",
        금액="보낼 금액을 입력하세요."
    )
    async def transfer_money(self, interaction: discord.Interaction, 받는사람: discord.Member, 금액: int):
        """다른 사람에게 돈을 보냅니다."""
        sender_id = interaction.user.id
        receiver_id = 받는사람.id

        # 응답이 길어질 수 있으므로 먼저 defer로 응답을 보류합니다.
        await interaction.response.defer()

        # 1. 보낼 금액이 0보다 큰지 확인
        if 금액 <= 0:
            await interaction.followup.send("송금할 금액은 0보다 커야 합니다.")
            return

        # 2. 자기 자신에게 송금하는지 확인
        if sender_id == receiver_id:
            await interaction.followup.send("자기 자신에게는 송금할 수 없습니다.")
            return
            
        # 3. 봇에게 송금하는지 확인
        if 받는사람.bot:
            await interaction.followup.send("봇에게는 돈을 보낼 수 없습니다. 🤖")
            return

        # 송금 진행 (SQLite, 원자적 트랜잭션)
        try:
            new_sender, new_receiver = db.transfer(sender_id, receiver_id, 금액)
        except ValueError as e:
            await interaction.followup.send(str(e))
            return

        embed = discord.Embed(
            title="💸 송금 완료",
            description=f"{interaction.user.mention}님이 {받는사람.mention}님에게 **{금액:,}원**을 보냈습니다.",
            color=discord.Color.green()
        )
        # defer를 사용했으므로 followup.send로 후속 메시지를 보냅니다.
        await interaction.followup.send(embed=embed)

    # 3. 랭킹: /돈 순위 [상위]
    @money.command(name="순위", description="소지금 상위 랭킹을 확인합니다(페이지 지원).")
    @app_commands.describe(상위="페이지당 표시 인원 (기본 10, 최대 25)")
    async def money_rank(self, interaction: discord.Interaction, 상위: int = 10):
        per_page = max(1, min(int(상위), 25))

        # 첫 페이지 계산
        total = db.count_users()
        total_pages = max(1, (total + per_page - 1) // per_page)

        def build_embed(page: int) -> discord.Embed:
            offset = (page - 1) * per_page
            rows = db.rank_page(offset, per_page)
            lines = []
            for i, (uid, bal) in enumerate(rows, start=1):
                member = None
                if interaction.guild:
                    member = interaction.guild.get_member(uid)
                user = member or interaction.client.get_user(uid)
                name = (
                    member.display_name if member
                    else (user.name if isinstance(user, discord.User) else f"<@{uid}>")
                )
                lines.append(f"**{offset + i}.** {name} — **{bal:,}원**")

            rank, my_balance, _ = db.get_rank(interaction.user.id)
            embed = discord.Embed(
                title="🏆 소지금 순위",
                description="\n".join(lines) if lines else "데이터가 없습니다.",
                color=discord.Color.purple(),
            )
            footer = f"당신의 순위: {rank} (보유 {my_balance:,}원) • 페이지 {page}/{total_pages} • ⬅️ ➡️ • 1분 후 만료"
            embed.set_footer(text=footer)
            return embed

        await interaction.response.send_message(embed=build_embed(1))
        msg = await interaction.original_response()

        if total == 0:
            return

        # 컨텍스트 저장
        self._rank_pages[msg.id] = {
            "owner_id": interaction.user.id,
            "per_page": per_page,
            "page": 1,
            "total_pages": total_pages,
            "expires_at": time.monotonic() + 60,
        }

        # 반응 추가
        for emoji in ("⬅️", "➡️"):
            try:
                await msg.add_reaction(emoji)
            except Exception:
                pass

        # 만료 스케줄링
        asyncio.create_task(self._expire_rank_message(msg))

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot:
            return
        msg = reaction.message
        ctx = self._rank_pages.get(msg.id)
        if not ctx:
            return
        # 소유자만 조작 가능
        if user.id != ctx["owner_id"]:
            return

        # 만료 확인
        if time.monotonic() > ctx.get("expires_at", 0):
            try:
                await msg.clear_reactions()
            except Exception:
                pass
            self._rank_pages.pop(msg.id, None)
            return

        emoji = str(reaction.emoji)
        page = ctx["page"]
        total_pages = ctx["total_pages"]
        if emoji == "⬅️" and page > 1:
            page -= 1
        elif emoji == "➡️" and page < total_pages:
            page += 1
        else:
            return

        ctx["page"] = page
        per_page = ctx["per_page"]

        # embed 재구성
        total = db.count_users()
        total_pages = max(1, (total + per_page - 1) // per_page)
        ctx["total_pages"] = total_pages

        def build_embed(page: int) -> discord.Embed:
            offset = (page - 1) * per_page
            rows = db.rank_page(offset, per_page)
            lines = []
            for i, (uid, bal) in enumerate(rows, start=1):
                member = msg.guild.get_member(uid) if msg.guild else None
                user_obj = member or self.bot.get_user(uid)
                name = (
                    member.display_name if member
                    else (user_obj.name if isinstance(user_obj, discord.User) else f"<@{uid}>")
                )
                lines.append(f"**{offset + i}.** {name} — **{bal:,}원**")
            rank, my_balance, _ = db.get_rank(user.id)
            embed = discord.Embed(title="🏆 소지금 순위", description="\n".join(lines) if lines else "데이터가 없습니다.", color=discord.Color.purple())
            embed.set_footer(text=f"당신의 순위: {rank} (보유 {my_balance:,}원) • 페이지 {page}/{total_pages} • ⬅️ ➡️ • 1분 후 만료")
            return embed

        try:
            await msg.edit(embed=build_embed(page))
        except Exception:
            pass
        try:
            await msg.remove_reaction(reaction.emoji, user)
        except Exception:
            pass

    async def _expire_rank_message(self, msg: discord.Message):
        await asyncio.sleep(60)
        ctx = self._rank_pages.get(msg.id)
        if not ctx:
            return
        # 현재 페이지 기준으로 임베드 만료 표기
        page = ctx["page"]
        per_page = ctx["per_page"]
        total = db.count_users()
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = (page - 1) * per_page
        rows = db.rank_page(offset, per_page)
        lines = []
        for i, (uid, bal) in enumerate(rows, start=1):
            member = msg.guild.get_member(uid) if msg.guild else None
            user_obj = member or self.bot.get_user(uid)
            name = (
                member.display_name if member
                else (user_obj.name if isinstance(user_obj, discord.User) else f"<@{uid}>")
            )
            lines.append(f"**{offset + i}.** {name} — **{bal:,}원**")
        rank, my_balance, _ = db.get_rank(ctx["owner_id"])
        embed = discord.Embed(title="🏆 소지금 순위", description="\n".join(lines) if lines else "데이터가 없습니다.", color=discord.Color.purple())
        embed.set_footer(text=f"당신의 순위: {rank} (보유 {my_balance:,}원) • 페이지 {page}/{total_pages} • 만료됨")
        try:
            await msg.edit(embed=embed)
        except Exception:
            pass
        try:
            await msg.clear_reactions()
        except Exception:
            pass
        self._rank_pages.pop(msg.id, None)

# 봇에 이 cog를 추가하기 위한 필수 함수
async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
