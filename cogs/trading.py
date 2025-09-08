import discord
from discord.ext import commands, tasks
from discord import app_commands

import database as db
from zoneinfo import ZoneInfo
from datetime import datetime
import time
import asyncio
import tempfile

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None


KST = ZoneInfo("Asia/Seoul")


SYMBOLS = [
    ("IDX_CHAT", "채팅 지수"),
    ("IDX_VOICE", "통화 지수"),
    ("IDX_REACT", "반응 지수"),
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
        # Ensure today's indices exist so 시세가 "없음"으로 뜨지 않도록 초기화
        try:
            db.ensure_indices_for_day(guild_id, date)
        except Exception:
            pass
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

    def _aggregate_candles(self, rows, timeframe: str, count: int):
        # rows: list[(ts, price)] UTC ts; align on KST boundaries
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        KST = ZoneInfo("Asia/Seoul")
        tf = timeframe
        buckets = {}
        order = []
        for ts, px in rows:
            dt = datetime.fromtimestamp(ts, KST)
            if tf == '분':
                key = dt.replace(second=0, microsecond=0)
            elif tf == '시간':
                key = dt.replace(minute=0, second=0, microsecond=0)
            elif tf == '일':
                key = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            else:  # '주' — ISO week start Monday
                monday = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
                key = monday
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append((ts, px))
        # Build candles in order
        candles = []
        for key in order:
            arr = buckets[key]
            arr.sort(key=lambda x: x[0])
            prices = [p for _, p in arr]
            o = prices[0]
            h = max(prices)
            l = min(prices)
            c = prices[-1]
            candles.append((key, o, h, l, c))
        # limit to most recent 'count'
        return candles[-count:]

    def _render_candles(self, symbol: str, candles, timeframe: str):
        if plt is None:
            return None
        import matplotlib.pyplot as plt  # already set to Agg
        from matplotlib.patches import Rectangle
        fig, ax = plt.subplots(figsize=(10, 4), dpi=150)
        ax.set_facecolor('white')
        xs = list(range(len(candles)))
        if not xs:
            return None
        highs = [h for _, _, h, _, _ in candles]
        lows = [l for _, _, _, l, _ in candles]
        ax.set_xlim(-0.5, len(xs) - 0.5)
        ax.set_ylim(min(lows) * 0.995, max(highs) * 1.005)
        w = 0.6
        for i, (_, o, h, l, c) in enumerate(candles):
            color = '#e74c3c' if c < o else '#2ecc71'
            # wick
            ax.vlines(i, l, h, color=color, linewidth=1)
            # body
            y = min(o, c)
            height = abs(c - o)
            if height == 0:
                height = max(highs) * 0.0005  # minimal visible body
            rect = Rectangle((i - w / 2, y), w, height, facecolor=color, edgecolor=color, linewidth=1)
            ax.add_patch(rect)
        ax.set_title(f"{symbol} {timeframe} 봉차트")
        ax.set_xticks([])
        ax.set_ylabel('가격')
        fig.tight_layout()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        fig.savefig(tmp.name, bbox_inches='tight')
        plt.close(fig)
        return tmp.name

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
        pos = db.list_instrument_holdings(interaction.user.id)
        if not pos:
            await interaction.response.send_message("보유 종목이 없습니다.", ephemeral=True)
            return
        lines = []
        total = 0
        for sym, qty in pos:
            try:
                px = db.get_symbol_price(interaction.guild.id, sym)
            except Exception:
                px = 0.0
            val = int(round(px * qty))
            total += val
            lines.append(f"`{sym}` ×{qty} • 현재 {px:.2f}원 • 평가 {val:,}원")
        embed = discord.Embed(title="보유 종목(인벤토리 기반)", description="\n".join(lines), color=discord.Color.green())
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
        # 마감 시에는 자동으로 개장시 시장가 예약주문 생성
        if not self._is_market_open():
            try:
                oid = db.create_order_market_open(interaction.guild.id, interaction.user.id, 종목, 'BUY', 수량)
            except ValueError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
            await interaction.response.send_message(f"시장 마감 중입니다. 개장 시 시장가 매수 예약이 접수되었습니다. (주문번호 {oid})", ephemeral=True)
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
        if not self._is_market_open():
            try:
                oid = db.create_order_market_open(interaction.guild.id, interaction.user.id, 종목, 'SELL', 수량)
            except ValueError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
            await interaction.response.send_message(f"시장 마감 중입니다. 개장 시 시장가 매도 예약이 접수되었습니다. (주문번호 {oid})", ephemeral=True)
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

    # -------- 예약주문 --------
    @group.command(name="예약매수", description="지정가로 매수 예약을 겁니다.")
    @app_commands.describe(종목="ETF 종목", 수량="매수 수량(정수)", 지정가="매수 지정가")
    async def limit_buy(self, interaction: discord.Interaction, 종목: str, 수량: int, 지정가: float):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        try:
            oid = db.create_order_limit(interaction.guild.id, interaction.user.id, 종목, 'BUY', 수량, 지정가)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message(f"지정가 매수 예약 접수: 주문번호 {oid}, {종목} ×{수량} @ {지정가:.2f}", ephemeral=True)

    @limit_buy.autocomplete("종목")
    async def _ac_lbuy(self, interaction: discord.Interaction, current: str):
        return self._symbol_choices(current)

    @group.command(name="예약매도", description="지정가로 매도 예약을 겁니다.")
    @app_commands.describe(종목="ETF 종목", 수량="매도 수량(정수)", 지정가="매도 지정가")
    async def limit_sell(self, interaction: discord.Interaction, 종목: str, 수량: int, 지정가: float):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        try:
            oid = db.create_order_limit(interaction.guild.id, interaction.user.id, 종목, 'SELL', 수량, 지정가)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message(f"지정가 매도 예약 접수: 주문번호 {oid}, {종목} ×{수량} @ {지정가:.2f}", ephemeral=True)

    @limit_sell.autocomplete("종목")
    async def _ac_lsell(self, interaction: discord.Interaction, current: str):
        return self._symbol_choices(current)

    @group.command(name="예약목록", description="내 예약 주문을 확인합니다.")
    async def list_orders(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        rows = db.list_user_orders(interaction.guild.id, interaction.user.id, status='OPEN')
        if not rows:
            await interaction.response.send_message("열려있는 예약 주문이 없습니다.", ephemeral=True)
            return
        lines = []
        for (oid, sym, side, qty, otype, lpx, status, cts) in rows:
            if otype == 'LIMIT':
                lines.append(f"#{oid} {side} {sym} ×{qty} @ {float(lpx):.2f} ({otype})")
            else:
                lines.append(f"#{oid} {side} {sym} ×{qty} (개장시장가)")
        embed = discord.Embed(title="예약 주문", description="\n".join(lines), color=discord.Color.purple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="차트", description="ETF 봉차트를 출력합니다.")
    @app_commands.describe(종목="ETF 종목", 단위="분/시간/일/주", 길이="캔들 개수(기본 60, 최대 240)")
    async def chart(self, interaction: discord.Interaction, 종목: str, 단위: str, 길이: int = 60):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if 단위 not in ("분", "시간", "일", "주"):
            await interaction.followup.send("단위는 분/시간/일/주 중 하나여야 합니다.", ephemeral=True)
            return
        count = max(5, min(int(길이), 240))
        # choose window
        now = int(time.time())
        sec = {"분": 60, "시간": 3600, "일": 86400, "주": 604800}[단위]
        since = now - sec * (count + 10)
        # 데이터 소스 분기: ETF_* 는 etf_ticks, IDX_* 는 activity_ticks
        # 통합: IDX_* 심볼은 ETF_*로 취급
        if 종목.startswith("IDX_"):
            mapping = {
                "IDX_CHAT": "ETF_CHAT",
                "IDX_VOICE": "ETF_VOICE",
                "IDX_REACT": "ETF_REACT",
            }
            종목 = mapping.get(종목.upper(), 종목)
        rows = db.get_etf_ticks_since(interaction.guild.id, 종목, since)
        if not rows:
            await interaction.followup.send("차트 데이터가 부족합니다.", ephemeral=True)
            return
        candles = self._aggregate_candles(rows, 단위, count)
        if len(candles) < 2:
            await interaction.followup.send("차트 데이터가 부족합니다.", ephemeral=True)
            return
        if plt is None:
            await interaction.followup.send("서버에 matplotlib가 설치되어 있지 않아 차트를 렌더링할 수 없습니다.", ephemeral=True)
            return
        # 렌더링은 별도 스레드에서 처리하여 이벤트 루프 블로킹 방지
        path = await asyncio.to_thread(self._render_candles, 종목, candles, 단위)
        if not path:
            await interaction.followup.send("차트 생성에 실패했습니다.", ephemeral=True)
            return
        file = discord.File(path, filename=f"{종목}_{단위}.png")
        embed = discord.Embed(title=f"{종목} {단위} 차트", color=discord.Color.teal())
        embed.set_image(url=f"attachment://{종목}_{단위}.png")
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    @chart.autocomplete("종목")
    async def _ac_chart_symbol(self, interaction: discord.Interaction, current: str):
        return self._symbol_choices(current)

    @group.command(name="예약취소", description="예약 주문을 취소합니다.")
    @app_commands.describe(주문번호="취소할 주문 번호")
    async def cancel_order(self, interaction: discord.Interaction, 주문번호: int):
        if not interaction.guild:
            await interaction.response.send_message("서버에서만 사용 가능합니다.", ephemeral=True)
            return
        ok = db.cancel_order(interaction.guild.id, interaction.user.id, 주문번호)
        if not ok:
            await interaction.response.send_message("취소할 수 없는 주문입니다.", ephemeral=True)
            return
        await interaction.response.send_message(f"주문 #{주문번호} 가 취소되었습니다.", ephemeral=True)

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
            # 주문 처리
            await self._process_orders_for_guild(guild.id, ts)

    async def _process_orders_for_guild(self, guild_id: int, ts: int):
        rows = db.list_open_orders_for_guild(guild_id)
        for (oid, user_id, symbol, side, qty, otype, lpx) in rows:
            try:
                px = db.get_symbol_price(guild_id, symbol)
            except Exception:
                continue
            try_exec = False
            if otype == 'MARKET_OPEN':
                try_exec = True
            elif otype == 'LIMIT':
                if side == 'BUY' and px <= float(lpx):
                    try_exec = True
                if side == 'SELL' and px >= float(lpx):
                    try_exec = True
            if not try_exec:
                continue
            # Attempt execution
            try:
                if side == 'BUY':
                    new_qty, price, notional, new_bal = db.trade_buy(guild_id, user_id, symbol, qty)
                    exec_price = price
                else:
                    rem_qty, price, proceeds, new_bal = db.trade_sell(guild_id, user_id, symbol, qty)
                    exec_price = price
                db.mark_order_filled(oid, ts, exec_price)
            except ValueError:
                # 조건 충족했지만 자금/보유량 부족 등으로 미체결 -> 보류 유지
                continue

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
