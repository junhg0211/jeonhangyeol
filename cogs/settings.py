import discord
from discord.ext import commands
from discord import app_commands

import db


class Settings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()

    group = app_commands.Group(name="설정", description="봇 설정")

    @group.command(name="알림채널", description="자동 알림을 보낼 채널을 설정/해제합니다.")
    @app_commands.describe(채널="알림을 보낼 텍스트 채널 (비우면 해제)")
    @app_commands.default_permissions(manage_guild=True)
    async def set_notify_channel(self, interaction: discord.Interaction, 채널: discord.TextChannel | None = None):
        if not interaction.guild:
            await interaction.response.send_message("서버 내에서만 사용할 수 있습니다.", ephemeral=True)
            return
        try:
            db.set_notify_channel(interaction.guild.id, 채널.id if 채널 else None)
        except Exception as e:
            await interaction.response.send_message(f"설정 중 오류: {e}", ephemeral=True)
            return
        if 채널:
            await interaction.response.send_message(f"알림 채널을 {채널.mention}(으)로 설정했습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("알림 채널 설정을 해제했습니다.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))

