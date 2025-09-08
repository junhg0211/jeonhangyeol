import discord
from discord.ext import commands
from discord import app_commands

import db


class Patent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()

    group = app_commands.Group(name="특허", description="특허 미니게임")

    @group.command(name="참가", description="특허 게임에 참가합니다.")
    async def join(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        db.join_patent_game(interaction.guild.id, interaction.user.id)
        await interaction.response.send_message("특허 게임에 참가했습니다.", ephemeral=True)

    @group.command(name="하차", description="특허 게임에서 하차합니다.")
    async def leave(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        db.leave_patent_game(interaction.guild.id, interaction.user.id)
        await interaction.response.send_message("특허 게임에서 하차했습니다.", ephemeral=True)

    @group.command(name="출원", description="단어에 대한 특허를 출원합니다.")
    @app_commands.describe(단어="특허 단어", 가격="사용료(짧을수록 최소가 높음)")
    async def file(self, interaction: discord.Interaction, 단어: str, 가격: int):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        w = (단어 or "").strip()
        if not w:
            await interaction.response.send_message("단어를 입력해 주세요.", ephemeral=True)
            return
        minp = db.patent_min_price(w)
        if 가격 < minp:
            await interaction.response.send_message(f"해당 단어의 최소 출원가: {minp:,}원", ephemeral=True)
            return
        try:
            pid = db.add_patent(interaction.guild.id, interaction.user.id, w, 가격)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message(f"특허 출원 완료: '{w}' (사용료 {가격:,}원)", ephemeral=True)

    @group.command(name="목록", description="출원된 특허 목록을 보여줍니다.")
    async def list_patents(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        rows = db.list_patents(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("등록된 특허가 없습니다.", ephemeral=True)
            return
        lines = []
        for owner_id, word, price in rows:
            member = interaction.guild.get_member(owner_id)
            name = member.display_name if member else f"<@{owner_id}>"
            lines.append(f"'{word}' — {price:,}원 (권자: {name})")
        embed = discord.Embed(title="📜 등록된 특허", description="\n".join(lines), color=discord.Color.gold())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="취소", description="내 특허를 취소합니다.")
    @app_commands.describe(단어="취소할 특허 단어")
    async def cancel(self, interaction: discord.Interaction, 단어: str):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        ok = db.cancel_patent(interaction.guild.id, interaction.user.id, 단어)
        if not ok:
            await interaction.response.send_message("해당 단어의 내 특허가 없습니다.", ephemeral=True)
            return
        await interaction.response.send_message(f"'{단어}' 특허를 취소했습니다.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only in guilds, non-bot, and participants
        if message.author.bot or not message.guild:
            return
        if not db.is_patent_participant(message.guild.id, message.author.id):
            return
        content = message.content or ""
        hits = db.find_patent_hits(message.guild.id, content)
        if not hits:
            return
        # Aggregate charges per owner, skip self-owned words
        charges = {}
        words = []
        for w, owner_id, price in hits:
            if owner_id == message.author.id:
                continue
            words.append(w)
            fee = db.patent_usage_fee(price)
            charges[owner_id] = charges.get(owner_id, 0) + int(fee)
        if not charges:
            return
        total = sum(charges.values())
        bal = db.get_balance(message.author.id)
        if bal >= total:
            # Pay all owners quietly (no reactions/messages)
            for owner_id, amount in charges.items():
                try:
                    db.transfer(message.author.id, owner_id, amount)
                except Exception:
                    pass
            try:
                db.log_patent_detection(
                    guild_id=message.guild.id,
                    user_id=message.author.id,
                    channel_id=message.channel.id,
                    message_id=message.id,
                    words=words,
                    total_fee=total,
                    censored=False,
                )
            except Exception:
                pass
            return
        # Not enough funds: censor
        censored = db.censor_words(content, words)
        info = (
            "이 메시지는 특허 미니게임 규칙에 따라 검열되었습니다.\n"
            "- 메시지에 등록된 특허 단어가 포함되고 잔액이 부족하면 단어가 스포일러로 숨겨집니다.\n"
            "- 참여: `/특허 참가` • 하차: `/특허 하차` • 특허 목록: `/특허 목록`\n"
            "- 단어 사용료는 해당 특허의 출원가의 1/50입니다."
        )
        try:
            await message.delete()
            await message.channel.send(
                content=f"{message.author.mention}\n{censored}\n\n{info}",
                suppress_embeds=True,
                allowed_mentions=discord.AllowedMentions(users=[message.author]),
            )
        except Exception:
            # Fallback: reply with censored version + info
            try:
                await message.reply(
                    content=f"{censored}\n\n{info}",
                    mention_author=False,
                    suppress_embeds=True,
                )
            except Exception:
                pass
        # log censored event
        try:
            db.log_patent_detection(
                guild_id=message.guild.id,
                user_id=message.author.id,
                channel_id=message.channel.id,
                message_id=message.id,
                words=words,
                total_fee=total,
                censored=True,
            )
        except Exception:
            pass

    @group.command(name="로그", description="최근 특허 검출 내역을 확인합니다.")
    @app_commands.describe(유저="특정 유저만 필터", 상위="표시 개수(기본 20, 최대 50)")
    async def logs(self, interaction: discord.Interaction, 유저: discord.Member | None = None, 상위: int = 20):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        limit = max(1, min(int(상위), 50))
        if 유저:
            rows = db.get_user_patent_logs(interaction.guild.id, 유저.id, limit)
        else:
            rows = db.get_recent_patent_logs(interaction.guild.id, limit)
        if not rows:
            await interaction.response.send_message("최근 검출 내역이 없습니다.", ephemeral=True)
            return
        lines = []
        for ts, uid, ch, mid, words, fee, censored in rows:
            member = interaction.guild.get_member(uid)
            name = member.display_name if member else f"<@{uid}>"
            ch_text = f" <#{ch}>" if ch else ""
            msg_link = f" [메시지]" if mid else ""
            state = "검열" if censored else "과금"
            lines.append(f"<t:{ts}:R> {name}{ch_text} — {state} • 단어: {words or '-'} • 합계 {fee:,}원")
        embed = discord.Embed(title="🧾 특허 검출 내역", description="\n".join(lines), color=discord.Color.dark_teal())
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Patent(bot))
