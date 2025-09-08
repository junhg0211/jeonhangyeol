from .core import get_conn, KST
from .inventory import instrument_item, grant_item, discard_item
from .activity import get_index_bounds, ensure_indices_for_day
import time
from datetime import datetime

INSTRUMENTS_DEFAULT = [
    ("IDX_CHAT", "채팅 지수", "INDEX", "chat"),
    ("IDX_VOICE", "통화 지수", "INDEX", "voice"),
    ("IDX_REACT", "반응 지수", "INDEX", "react"),
    ("ETF_ALL", "종합 ETF", "ETF", "all"),
]


def ensure_instruments():
    with get_conn() as conn:
        for sym, name, kind, cat in INSTRUMENTS_DEFAULT:
            conn.execute("INSERT OR IGNORE INTO instruments(symbol, name, kind, category) VALUES(?, ?, ?, ?)", (sym, name, kind, cat))


def normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper()
    if s in ("ETF_CHAT", "ETF_VOICE", "ETF_REACT"):
        return s.replace("ETF_", "IDX_")
    if s == "IDX_ALL":
        return "ETF_ALL"
    return s


def get_symbol_price(guild_id: int, symbol: str, ts: int | None = None) -> float:
    symbol = normalize_symbol(symbol)
    # Resolve current KST date and ensure indices exist for the day
    date_kst = datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d") if ts is not None else datetime.now(KST).strftime("%Y-%m-%d")
    try:
        ensure_indices_for_day(guild_id, date_kst)
    except Exception:
        pass
    if symbol == "IDX_CHAT":
        cur, _, _ = get_index_bounds(guild_id, date_kst, "chat")
        return cur
    if symbol == "IDX_VOICE":
        cur, _, _ = get_index_bounds(guild_id, date_kst, "voice")
        return cur
    if symbol == "IDX_REACT":
        cur, _, _ = get_index_bounds(guild_id, date_kst, "react")
        return cur
    if symbol == "ETF_ALL":
        c1, _, _ = get_index_bounds(guild_id, date_kst, "chat")
        c2, _, _ = get_index_bounds(guild_id, date_kst, "voice")
        c3, _, _ = get_index_bounds(guild_id, date_kst, "react")
        return (c1 + c2 + c3) / 3.0
    raise ValueError("Unknown symbol")


def trade_buy(guild_id: int, user_id: int, symbol: str, qty: int) -> tuple[int, float, int, int]:
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    price = float(get_symbol_price(guild_id, symbol))
    notional = int(round(price * qty))
    ts = int(time.time())
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("SELECT balance FROM balances WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        bal = int(row[0]) if row else 0
        if row is None:
            bal = 1000
            conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (user_id, bal))
        if bal < notional:
            raise ValueError("잔액이 부족합니다.")
        conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (bal - notional, user_id))
    emo, name = instrument_item(symbol)
    new_qty = grant_item(user_id, name, emo, qty)
    with get_conn() as conn:
        conn.execute("INSERT INTO trades(ts, guild_id, user_id, symbol, side, qty, price, notional) VALUES(?, ?, ?, ?, 'BUY', ?, ?, ?)", (ts, guild_id, user_id, normalize_symbol(symbol), qty, price, notional))
        new_bal = int(conn.execute("SELECT balance FROM balances WHERE user_id=?", (user_id,)).fetchone()[0])
    return new_qty, price, notional, new_bal


def trade_sell(guild_id: int, user_id: int, symbol: str, qty: int) -> tuple[int, float, int, int]:
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    price = float(get_symbol_price(guild_id, symbol))
    proceeds = int(round(price * qty))
    ts = int(time.time())
    emo, name = instrument_item(symbol)
    remaining = discard_item(user_id, name, emo, qty)
    with get_conn() as conn:
        cur = conn.execute("SELECT balance FROM balances WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        bal = int(row[0]) if row else 0
        if row is None:
            conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (user_id, 0))
        new_bal = bal + proceeds
        conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (new_bal, user_id))
        conn.execute("INSERT INTO trades(ts, guild_id, user_id, symbol, side, qty, price, notional) VALUES(?, ?, ?, ?, 'SELL', ?, ?, ?)", (ts, guild_id, user_id, normalize_symbol(symbol), qty, price, proceeds))
    return remaining, price, proceeds, new_bal


def get_last_etf_price(guild_id: int, symbol: str) -> float | None:
    with get_conn() as conn:
        cur = conn.execute("SELECT price FROM etf_ticks WHERE guild_id=? AND symbol=? ORDER BY ts DESC LIMIT 1", (guild_id, normalize_symbol(symbol)))
        row = cur.fetchone()
        return float(row[0]) if row else None


def record_etf_tick(guild_id: int, ts: int, symbol: str, price: float, delta: float) -> None:
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO etf_ticks(guild_id, ts, symbol, price, delta) VALUES(?, ?, ?, ?, ?)", (guild_id, ts, normalize_symbol(symbol), float(price), float(delta)))

__all__ = ['ensure_instruments','normalize_symbol','get_symbol_price','trade_buy','trade_sell','get_last_etf_price','record_etf_tick']
