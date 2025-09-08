import discord
from discord.ext import commands, tasks
from discord import app_commands

import db
from zoneinfo import ZoneInfo
from datetime import datetime
import time


KST = ZoneInfo("Asia/Seoul")


SYMBOLS = [
    ("ETF_CHAT", "채팅 ETF"),
    ("ETF_VOICE", "통화 ETF"),
    ("ETF_REACT", "반응 ETF"),
    ("ETF_ALL", "종합 ETF"),
]


class Trading(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        db.init_db()
        db.ensure_instruments()
        # start background recording after ready

    group = app_commands.Group(name="투자", description="활동 지수 투자")

    def _is_market_open(self) -> bool:
        now = datetime.now(KST)
        t = now.time()
        return (t >= datetime.strptime("09:00", "%H:%M").time()) and (t < datetime.strptime("21:00", "%H:%M").time())

    async def _quote_embed(self, guild_id: int) -> discord.Embed:
        date = datetime.now(KST).strftime("%Y-%m-%d")
        rows = []
        for sym, name in SYMBOLS:
            try:
                px = db.get_symbol_price(guild_id, sym)
            except Exception:
                px = None
            rows.append((sym, name, px))
        desc = []
        for sym, name, px in rows:
            if px is None:
                desc.append(f"`{sym}` {name}: 시세 없음")
            else:
                desc.append(f"`{sym}` {name}: {px:.2f}원")
        embed = discord.Embed(title="시세", description="\n".join(desc), color=discord.Color.teal())
        embed.set_footer(text=f"{date} KST • 시장 {'개장' if self._is_market_open() else '마감'}")
        return embed

    @group.command(name="시세", description="현재 시세를 확인합니다.")
    async def quote(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        embed = await self._quote_embed(interaction.guild.id)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="보유", description="보유 종목과 평가금액을 확인합니다.")
    async def holdings(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        pos = db.list_positions(interaction.guild.id, interaction.user.id)
        if not pos:
            await interaction.response.send_message("보유 종목이 없습니다.", ephemeral=True)
            return
        lines = []
        total = 0
        for sym, qty, avg in pos:
            try:
                px = db.get_symbol_price(interaction.guild.id, sym)
            except Exception:
                px = 0.0
            val = int(round(px * qty))
            total += val
            pnl = (px - avg) * qty
            lines.append(f"`{sym}` ×{qty} • 평균 {avg:.2f}원 • 현재 {px:.2f}원 • 평가 {val:,}원 • 손익 {pnl:+.2f}")
        embed = discord.Embed(title="보유 종목", description="\n".join(lines), color=discord.Color.green())
        embed.set_footer(text=f"총 평가금액: {total:,}원")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _symbol_choices(self, current: str):
        cur = (current or "").upper()
        out = []
        for sym, name in SYMBOLS:
            if cur in sym or cur in name.upper():
                out.append(app_commands.Choice(name=f"{sym} — {name}", value=sym))
        return out[:25]

    @group.command(name="매수", description="지정 수량을 시장가로 매수합니다.")
    @app_commands.describe(종목="ETF 종목", 수량="매수 수량(정수)")
    async def buy(self, interaction: discord.Interaction, 종목: str, 수량: int):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        try:
            new_qty, price, notional, new_bal = db.trade_buy(interaction.guild.id, interaction.user.id, 종목, 수량)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        embed = discord.Embed(title="매수 체결", color=discord.Color.blue())
        embed.add_field(name="종목", value=종목)
        embed.add_field(name="체결가", value=f"{price:.2f}원")
        embed.add_field(name="수량", value=f"{수량}")
        embed.add_field(name="금액", value=f"{notional:,}원")
        embed.set_footer(text=f"보유 수량: {new_qty} • 잔액: {new_bal:,}원")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @buy.autocomplete("종목")
    async def _ac_buy(self, interaction: discord.Interaction, current: str):
        return self._symbol_choices(current)

    @group.command(name="매도", description="지정 수량을 시장가로 매도합니다.")
    @app_commands.describe(종목="ETF 종목", 수량="매도 수량(정수)")
    async def sell(self, interaction: discord.Interaction, 종목: str, 수량: int):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        try:
            new_qty, price, proceeds, new_bal = db.trade_sell(interaction.guild.id, interaction.user.id, 종목, 수량)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        embed = discord.Embed(title="매도 체결", color=discord.Color.orange())
        embed.add_field(name="종목", value=종목)
        embed.add_field(name="체결가", value=f"{price:.2f}원")
        embed.add_field(name="수량", value=f"{수량}")
        embed.add_field(name="금액", value=f"{proceeds:,}원")
        embed.set_footer(text=f"잔여 수량: {new_qty} • 잔액: {new_bal:,}원")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @sell.autocomplete("종목")
    async def _ac_sell(self, interaction: discord.Interaction, current: str):
        return self._symbol_choices(current)

    # -------- ETF minute ticks --------
    @tasks.loop(seconds=60)
    async def etf_minute_tick(self):
        if not self._is_market_open():
            return
        ts = int(time.time())
        for guild in list(self.bot.guilds):
            for sym, _ in SYMBOLS:
                try:
                    px = db.get_symbol_price(guild.id, sym)
                except Exception:
                    continue
                prev = db.get_last_etf_price(guild.id, sym) or px
                delta = px - prev
                try:
                    db.record_etf_tick(guild.id, ts, sym, px, delta)
                except Exception:
                    pass

    @etf_minute_tick.before_loop
    async def before_etf_minute_tick(self):
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.etf_minute_tick.is_running():
            self.etf_minute_tick.start()


async def setup(bot: commands.Bot):
    await bot.add_cog(Trading(bot))
