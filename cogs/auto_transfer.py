import discord
from discord.ext import commands, tasks
from discord import app_commands

import db
from zoneinfo import ZoneInfo
from datetime import datetime


KST = ZoneInfo("Asia/Seoul")


class AutoTransfer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()

    group = app_commands.Group(name="자동이체", description="주기적 송금 설정")

    @group.command(name="추가", description="주기적 송금을 등록합니다.")
    @app_commands.describe(대상="송금 받을 대상", 금액="송금 금액", 주기일="며칠마다(1~365)", 시작일="KST 기준 YYYY-MM-DD, 기본은 오늘")
    async def add(self, interaction: discord.Interaction, 대상: discord.Member, 금액: int, 주기일: int, 시작일: str | None = None):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        if 대상.bot:
            await interaction.response.send_message("봇에게는 송금할 수 없습니다.", ephemeral=True)
            return
        if 대상.id == interaction.user.id:
            await interaction.response.send_message("자기 자신에게는 설정할 수 없습니다.", ephemeral=True)
            return
        if 금액 <= 0:
            await interaction.response.send_message("금액은 0보다 커야 합니다.", ephemeral=True)
            return
        if 주기일 < 1 or 주기일 > 365:
            await interaction.response.send_message("주기는 1~365일 범위여야 합니다.", ephemeral=True)
            return
        today = datetime.now(KST).strftime("%Y-%m-%d")
        sdate = 시작일 or today
        try:
            _ = datetime.strptime(sdate, "%Y-%m-%d")
        except Exception:
            await interaction.response.send_message("시작일 형식이 올바르지 않습니다. YYYY-MM-DD", ephemeral=True)
            return
        try:
            auto_id = db.create_auto_transfer(interaction.guild.id, interaction.user.id, 대상.id, 금액, 주기일, sdate)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message(f"자동이체 등록 완료: #{auto_id} — {주기일}일마다 {대상.mention}에게 {금액:,}원 (시작 {sdate})", ephemeral=True)

    @group.command(name="목록", description="내 자동이체 설정 목록")
    async def list_(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        rows = db.list_user_auto_transfers(interaction.guild.id, interaction.user.id)
        if not rows:
            await interaction.response.send_message("등록된 자동이체가 없습니다.", ephemeral=True)
            return
        lines = []
        today = datetime.now(KST).strftime("%Y-%m-%d")
        for (aid, to_user, amount, period, sdate, ldate, active) in rows:
            m = interaction.guild.get_member(int(to_user))
            name = m.display_name if m else f"<@{to_user}>"
            status = "활성" if int(active) == 1 else "비활성"
            # next due 계산
            next_due = "-"
            try:
                days = db._days_between_kst(sdate, today)
                if ldate == today:
                    days += 1
                rem = (int(period) - (days % int(period))) % int(period)
                next_due = today if rem == 0 else (datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=KST) + __import__("datetime").timedelta(days=rem)).strftime("%Y-%m-%d")
            except Exception:
                pass
            lines.append(f"#{aid} → {name}: {amount:,}원 / {period}일마다 • 시작 {sdate} • 마지막 {ldate or '-'} • 다음 {next_due} • {status}")
        embed = discord.Embed(title="🔁 자동이체 목록", description="\n".join(lines), color=discord.Color.teal())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="취소", description="자동이체를 취소합니다.")
    @app_commands.describe(번호="/자동이체 목록에서 확인한 번호")
    async def cancel(self, interaction: discord.Interaction, 번호: int):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        ok = db.cancel_auto_transfer(interaction.guild.id, interaction.user.id, 번호)
        if not ok:
            await interaction.response.send_message("취소할 수 없거나 이미 취소된 항목입니다.", ephemeral=True)
            return
        await interaction.response.send_message(f"자동이체 #{번호} 가 취소되었습니다.", ephemeral=True)

    # 실행 루프: 30분마다 KST 기준 당일분 수행 여부 확인
    @tasks.loop(minutes=30)
    async def runner(self):
        today = datetime.now(KST).strftime("%Y-%m-%d")
        try:
            due = db.list_due_auto_transfers(today)
        except Exception:
            due = []
        for auto_id, gid, frm, to, amount in due:
            # 송금 시도
            try:
                db.transfer(frm, to, amount)
                db.mark_auto_transfer_run(auto_id, True, None, today)
            except Exception as e:
                db.mark_auto_transfer_run(auto_id, False, str(e), None)
                # 실패 알림: 보낸 사람에게 DM, 실패 시 알림 채널로
                try:
                    guild = self.bot.get_guild(gid)
                    sender = guild.get_member(frm) if guild else None
                    recipient = guild.get_member(to) if guild else None
                    rname = recipient.display_name if recipient else f"<@{to}>"
                    msg = f"자동이체 실패: {rname}에게 {amount:,}원 전송하지 못했습니다.\n사유: {str(e)}"
                    if sender:
                        try:
                            await sender.send(msg)
                            continue
                        except Exception:
                            pass
                    # DM 실패 시 알림 채널로
                    ch_id = db.get_notify_channel(gid)
                    if ch_id:
                        ch = self.bot.get_channel(ch_id)
                        if isinstance(ch, (discord.TextChannel, discord.Thread)):
                            try:
                                prefix = sender.mention + "\n" if sender else ""
                                await ch.send(prefix + msg)
                            except Exception:
                                pass
                except Exception:
                    pass

    @runner.before_loop
    async def before_runner(self):
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.runner.is_running():
            self.runner.start()


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoTransfer(bot))
