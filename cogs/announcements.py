import discord
from discord.ext import commands
from discord import app_commands

import database as db


class Announcements(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()

    group = app_commands.Group(name="공지", description="메인 채팅/공지사항 설정 및 관리")

    # 채널 설정
    @group.command(name="메인채팅", description="메인 채팅 채널을 설정/해제합니다.")
    @app_commands.describe(채널="메인 채팅 채널(비우면 해제)")
    @app_commands.default_permissions(manage_guild=True)
    async def set_main_chat(self, interaction: discord.Interaction, 채널: discord.TextChannel | None = None):
        if not interaction.guild:
            await interaction.response.send_message("서버 내에서만 사용 가능합니다.", ephemeral=True)
            return
        db.set_main_chat_channel(interaction.guild.id, 채널.id if 채널 else None)
        if 채널:
            await interaction.response.send_message(f"메인 채팅 채널을 {채널.mention}(으)로 설정했습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("메인 채팅 채널 설정을 해제했습니다.", ephemeral=True)

    @group.command(name="공지채널", description="공지사항을 보낼 채널을 설정/해제합니다.")
    @app_commands.describe(채널="공지 채널(비우면 메인 채팅으로 보냄)")
    @app_commands.default_permissions(manage_guild=True)
    async def set_announce_channel(self, interaction: discord.Interaction, 채널: discord.TextChannel | None = None):
        if not interaction.guild:
            await interaction.response.send_message("서버 내에서만 사용 가능합니다.", ephemeral=True)
            return
        db.set_announce_channel(interaction.guild.id, 채널.id if 채널 else None)
        if 채널:
            await interaction.response.send_message(f"공지 채널을 {채널.mention}(으)로 설정했습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("공지 채널 설정을 해제했습니다.", ephemeral=True)

    # 공지 관리
    @group.command(name="등록", description="공지사항을 등록합니다.")
    @app_commands.describe(내용="공지 텍스트")
    @app_commands.default_permissions(manage_guild=True)
    async def add_notice(self, interaction: discord.Interaction, 내용: str):
        if not interaction.guild:
            await interaction.response.send_message("서버 내에서만 사용 가능합니다.", ephemeral=True)
            return
        nid = db.add_announcement(interaction.guild.id, 내용)
        await interaction.response.send_message(f"공지 등록 완료: #{nid}", ephemeral=True)

    @group.command(name="목록", description="등록된 공지사항 목록을 확인합니다.")
    @app_commands.default_permissions(manage_guild=True)
    async def list_notices(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버 내에서만 사용 가능합니다.", ephemeral=True)
            return
        rows = db.list_announcements(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("등록된 공지사항이 없습니다.", ephemeral=True)
            return
        lines = [f"#{i} — {('활성' if a==1 else '비활성')} — {c[:120]}" for (i, c, a) in rows]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="삭제", description="공지사항을 삭제합니다.")
    @app_commands.describe(번호="삭제할 공지 번호")
    @app_commands.default_permissions(manage_guild=True)
    async def remove_notice(self, interaction: discord.Interaction, 번호: int):
        if not interaction.guild:
            await interaction.response.send_message("서버 내에서만 사용 가능합니다.", ephemeral=True)
            return
        ok = db.remove_announcement(interaction.guild.id, 번호)
        if not ok:
            await interaction.response.send_message("해당 번호의 공지가 없습니다.", ephemeral=True)
            return
        await interaction.response.send_message(f"공지 #{번호} 삭제 완료", ephemeral=True)

    @group.command(name="초기화", description="공지사항을 모두 삭제합니다.")
    @app_commands.default_permissions(manage_guild=True)
    async def clear_notices(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버 내에서만 사용 가능합니다.", ephemeral=True)
            return
        db.clear_announcements(interaction.guild.id)
        await interaction.response.send_message("모든 공지를 삭제했습니다.", ephemeral=True)

    # 메세지 카운터 및 로테이션 송출
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        main_ch = db.get_main_chat_channel(message.guild.id)
        if not main_ch or message.channel.id != main_ch:
            return
        if not db.has_announcements(message.guild.id):
            return
        count = db.incr_message_count(message.guild.id, message.channel.id)
        if count % 50 != 0:
            return
        index = (count // 50) - 1
        content = db.next_announcement(message.guild.id, index)
        if not content:
            return
        # Announce channel override
        dest_id = db.get_announce_channel(message.guild.id) or message.channel.id
        dest = self.bot.get_channel(dest_id)
        if isinstance(dest, (discord.TextChannel, discord.Thread)):
            try:
                await dest.send(content)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Announcements(bot))
