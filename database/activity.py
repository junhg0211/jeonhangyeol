from .core import get_conn, KST
import time as _time
from datetime import datetime


def _today_kst(ts: int | None = None) -> str:
    dt = datetime.fromtimestamp(ts, KST) if ts is not None else datetime.now(KST)
    return dt.strftime("%Y-%m-%d")


def ensure_indices_for_day(guild_id: int, date_kst: str | None = None) -> None:
    date_kst = date_kst or _today_kst()
    cats = ("chat", "voice", "react")
    now = int(_time.time())
    with get_conn() as conn:
        for c in cats:
            cur = conn.execute("SELECT 1 FROM activity_indices WHERE guild_id=? AND date=? AND category=?", (guild_id, date_kst, c))
            if cur.fetchone():
                continue
            # previous close
            curp = conn.execute("SELECT current_idx FROM activity_indices WHERE guild_id=? AND category=? ORDER BY date DESC LIMIT 1", (guild_id, c))
            row = curp.fetchone()
            open_idx = float(row[0]) if row else 100.0
            lower = open_idx * 0.5
            upper = open_idx * 2.0
            conn.execute(
                """
                INSERT INTO activity_indices(guild_id, date, category, open_idx, current_idx, lower_bound, upper_bound, opened_at, high_idx, low_idx)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, date_kst, c, open_idx, open_idx, lower, upper, now, open_idx, open_idx),
            )


def update_activity_tick(guild_id: int, ts: int, category: str, idx_value: float, delta: float, chat_count: int, react_count: int, voice_count: int, date_kst: str | None = None) -> None:
    date_kst = date_kst or _today_kst(ts)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO activity_ticks(guild_id, ts, date, category, idx_value, delta, chat_count, react_count, voice_count)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, ts, date_kst, category, idx_value, delta, chat_count, react_count, voice_count),
        )
        conn.execute(
            """
            UPDATE activity_indices
            SET current_idx=?,
                high_idx=CASE WHEN high_idx IS NULL OR ? > high_idx THEN ? ELSE high_idx END,
                low_idx=CASE WHEN low_idx IS NULL OR ? < low_idx THEN ? ELSE low_idx END
            WHERE guild_id=? AND date=? AND category=?
            """,
            (idx_value, idx_value, idx_value, idx_value, idx_value, guild_id, date_kst, category),
        )


def get_index_bounds(guild_id: int, date_kst: str, category: str) -> tuple[float, float, float]:
    with get_conn() as conn:
        cur = conn.execute("SELECT open_idx, lower_bound, upper_bound, current_idx FROM activity_indices WHERE guild_id=? AND date=? AND category=?", (guild_id, date_kst, category))
        row = cur.fetchone()
        if not row:
            raise ValueError("Index not initialised")
        open_idx, lower, upper, current = row
        return float(current), float(lower), float(upper)


def get_index_info(guild_id: int, date_kst: str, category: str):
    with get_conn() as conn:
        cur = conn.execute("SELECT current_idx, lower_bound, upper_bound, high_idx, low_idx, open_idx FROM activity_indices WHERE guild_id=? AND date=? AND category=?", (guild_id, date_kst, category))
        row = cur.fetchone()
        if not row:
            raise ValueError("Index not initialised")
        current, lower, upper, high, low, open_idx = row
        return float(current), float(lower), float(upper), (float(high) if high is not None else None), (float(low) if low is not None else None), float(open_idx)


def get_etf_ticks_since(guild_id: int, symbol: str, since_ts: int):
    with get_conn() as conn:
        cur = conn.execute("SELECT ts, price FROM etf_ticks WHERE guild_id=? AND symbol=? AND ts>=? ORDER BY ts ASC", (guild_id, symbol, since_ts))
        return [(int(ts), float(px)) for (ts, px) in cur.fetchall()]


def get_index_ticks_since(guild_id: int, category: str, since_ts: int):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT ts, idx_value FROM activity_ticks WHERE guild_id=? AND category=? AND ts>=? ORDER BY ts ASC",
            (guild_id, category, since_ts),
        )
        return [(int(ts), float(px)) for (ts, px) in cur.fetchall()]


def get_activity_totals(guild_id: int, category: str, start_ts: int, end_ts: int) -> tuple[int, int, int]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT COALESCE(SUM(chat_count),0), COALESCE(SUM(react_count),0), COALESCE(SUM(voice_count),0)
            FROM activity_ticks WHERE guild_id=? AND category=? AND ts BETWEEN ? AND ?
            """,
            (guild_id, category, int(start_ts), int(end_ts)),
        )
        row = cur.fetchone()
        return int(row[0]), int(row[1]), int(row[2])

__all__ = [
    'ensure_indices_for_day','update_activity_tick','get_index_bounds','get_index_info','get_etf_ticks_since','get_index_ticks_since','get_activity_totals'
]
