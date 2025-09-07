# cogs/economy.py

import discord
from discord import app_commands  # app_commands를 import 합니다.
from discord.ext import commands
import db

class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Ensure DB is ready on cog init
        db.init_db()

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
    @money.command(name="순위", description="소지금 상위 랭킹을 확인합니다.")
    @app_commands.describe(상위="표시할 인원 수 (기본 10, 최대 50)")
    async def money_rank(self, interaction: discord.Interaction, 상위: int = 10):
        # 값 검증 및 상한 적용
        top_n = max(1, min(int(상위), 50))

        await interaction.response.defer()

        rows = db.top_balances(top_n)

        # 유저명 해석
        lines = []
        for idx, (uid, bal) in enumerate(rows, start=1):
            user = interaction.client.get_user(uid) or (
                interaction.guild.get_member(uid) if interaction.guild else None
            )
            name = user.display_name if isinstance(user, discord.Member) else (
                user.name if isinstance(user, discord.User) else f"<@{uid}>"
            )
            lines.append(f"**{idx}.** {name} — **{bal:,}원**")

        # 호출자 개인 순위도 제공
        rank, my_balance, total = db.get_rank(interaction.user.id)

        embed = discord.Embed(
            title="🏆 소지금 순위",
            description="\n".join(lines) if lines else "데이터가 없습니다.",
            color=discord.Color.purple()
        )
        embed.set_footer(text=f"당신의 순위: {rank}/{total} (보유 {my_balance:,}원)")

        await interaction.followup.send(embed=embed)

# 봇에 이 cog를 추가하기 위한 필수 함수
async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
