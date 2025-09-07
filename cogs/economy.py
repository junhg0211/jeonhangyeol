# cogs/economy.py

import discord
from discord.ext import commands

class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 간단한 데이터 저장을 위해 딕셔너리를 사용합니다.
        # {유저_ID: 소지금} 형태로 저장됩니다.
        self.user_balances = {}

    # 소지금이 있는지 확인하고, 없으면 기본값을 설정하는 함수
    def get_balance(self, user_id: int):
        return self.user_balances.setdefault(user_id, 1000) # 기본 소지금 1000

    @commands.Cog.listener()
    async def on_ready(self):
        print("Economy cog가 준비되었습니다.")

    # 1. 소지금 확인 명령어 (!돈, !지갑)
    @commands.command(name='돈', aliases=['지갑'])
    async def check_balance(self, ctx):
        """자신의 소지금을 확인합니다."""
        user_id = ctx.author.id
        balance = self.get_balance(user_id)
        
        embed = discord.Embed(
            title=f"{ctx.author.display_name}님의 지갑",
            description=f"💰 현재 소지금: **{balance:,}원**",
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)

    # 2. 송금 명령어 (!송금 [상대방] [금액])
    @commands.command(name='송금')
    async def transfer_money(self, ctx, receiver: discord.Member, amount: int):
        """다른 사람에게 돈을 보냅니다. (!송금 @유저 100)"""
        sender_id = ctx.author.id
        receiver_id = receiver.id

        # 1. 보낼 금액이 0보다 큰지 확인
        if amount <= 0:
            await ctx.send("송금할 금액은 0보다 커야 합니다.")
            return

        # 2. 자기 자신에게 송금하는지 확인
        if sender_id == receiver_id:
            await ctx.send("자기 자신에게는 송금할 수 없습니다.")
            return
            
        # 3. 봇에게 송금하는지 확인
        if receiver.bot:
            await ctx.send("봇에게는 돈을 보낼 수 없습니다. 🤖")
            return

        sender_balance = self.get_balance(sender_id)

        # 4. 보내는 사람의 잔액이 충분한지 확인
        if sender_balance < amount:
            await ctx.send(f"소지금이 부족합니다. (현재 소지금: {sender_balance:,}원)")
            return

        # 송금 진행
        self.user_balances[sender_id] -= amount
        self.user_balances.setdefault(receiver_id, 1000) # 받는 사람이 돈이 없었을 경우 대비
        self.user_balances[receiver_id] += amount

        embed = discord.Embed(
            title="💸 송금 완료",
            description=f"{ctx.author.mention}님이 {receiver.mention}님에게 **{amount:,}원**을 보냈습니다.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        
    # 송금 명령어 오류 처리
    @transfer_money.error
    async def transfer_money_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("사용법: `!송금 @멘션 [금액]`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("올바른 유저를 멘션하거나 정확한 금액을 입력해주세요.")
        else:
            await ctx.send(f"오류가 발생했습니다: {error}")

# 봇에 이 cog를 추가하기 위한 필수 함수
async def setup(bot):
    await bot.add_cog(Economy(bot))
