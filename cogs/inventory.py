import discord
from discord import app_commands
from discord.ext import commands

import db


class Inventory(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()

    @commands.Cog.listener()
    async def on_ready(self):
        print("Inventory cog가 준비되었습니다.")

    # 1) 인벤토리 조회: /인벤토리 [유저]
    @app_commands.command(name="인벤토리", description="유저의 인벤토리를 확인합니다.")
    @app_commands.describe(유저="확인할 대상 (기본: 본인)")
    async def inventory(self, interaction: discord.Interaction, 유저: discord.Member | None = None):
        target = 유저 or interaction.user
        rows = db.list_inventory(target.id)

        if not rows:
            desc = "가지고 있는 아이템이 없습니다."
        else:
            lines = [f"{emoji} {name} × **{qty}**" for (emoji, name, qty) in rows]
            desc = "\n".join(lines)

        embed = discord.Embed(
            title=f"🎒 {target.display_name}님의 인벤토리",
            description=desc,
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=(target.id == interaction.user.id))

    # 2) 아이템 양도: /양도 받는사람 이모지 이름 [수량]
    @app_commands.command(name="양도", description="아이템을 다른 사람에게 전달합니다.")
    @app_commands.describe(
        받는사람="아이템을 받을 대상",
        이모지="아이템 이모지",
        이름="아이템 이름",
        수량="전달할 수량 (기본 1)"
    )
    async def give_item(
        self,
        interaction: discord.Interaction,
        받는사람: discord.Member,
        이모지: str,
        이름: str,
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

        await interaction.response.defer()

        try:
            sender_qty, receiver_qty = db.transfer_item(
                sender_id=interaction.user.id,
                receiver_id=받는사람.id,
                name=이름,
                emoji=이모지,
                qty=수량,
            )
        except ValueError as e:
            await interaction.followup.send(str(e))
            return

        embed = discord.Embed(
            title="🎁 아이템 양도 완료",
            description=(
                f"{interaction.user.mention}님이 {받는사람.mention}님에게\n"
                f"{이모지} {이름} × **{수량}** 을(를) 전달했습니다."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"보유수량: 보낸사람 {sender_qty}개 / 받은사람 {receiver_qty}개")
        await interaction.followup.send(embed=embed)

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


async def setup(bot: commands.Bot):
    await bot.add_cog(Inventory(bot))
