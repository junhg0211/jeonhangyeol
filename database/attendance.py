from .core import get_conn, KST
from datetime import datetime, timedelta
import time


def _today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def _yesterday_kst() -> str:
    return (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")


def attendance_check_in(guild_id: int, user_id: int) -> tuple[bool, int, int, int]:
    today = _today_kst()
    yday = _yesterday_kst()
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("SELECT last_date, streak, max_streak, total_days FROM attendance WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        row = cur.fetchone()
        if row is None:
            last_date, streak, maxs, total = None, 0, 0, 0
        else:
            last_date, streak, maxs, total = (row[0], int(row[1]), int(row[2]), int(row[3]))
        if last_date == today:
            return True, streak, 0, maxs
        new_streak = streak + 1 if last_date == yday else 1
        reward = 10 * new_streak
        maxs = max(maxs, new_streak)
        total += 1
        conn.execute(
            """
            INSERT INTO attendance(guild_id, user_id, last_date, streak, max_streak, total_days)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET last_date=excluded.last_date, streak=excluded.streak, max_streak=excluded.max_streak, total_days=excluded.total_days
            """,
            (guild_id, user_id, today, new_streak, maxs, total),
        )
        # award
        cur = conn.execute("SELECT balance FROM balances WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        bal = int(row[0]) if row else 0
        if row is None:
            conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (user_id, 0))
        conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (bal + reward, user_id))
        conn.execute("INSERT INTO attendance_logs(ts, guild_id, user_id, date, reward) VALUES(?, ?, ?, ?, ?)", (now, guild_id, user_id, today, reward))
        return False, new_streak, reward, maxs


def attendance_today(guild_id: int):
    today = _today_kst()
    with get_conn() as conn:
        cur = conn.execute("SELECT user_id, last_date, streak FROM attendance WHERE guild_id=?", (guild_id,))
        checked, not_checked = [], []
        for uid, last_date, streak in cur.fetchall():
            (checked if last_date == today else not_checked).append((int(uid), int(streak) if streak is not None else 0))
        return checked, not_checked


def attendance_max_streak_leaderboard(guild_id: int, limit: int = 20):
    with get_conn() as conn:
        cur = conn.execute("SELECT user_id, max_streak, total_days FROM attendance WHERE guild_id=? ORDER BY max_streak DESC, total_days DESC, user_id ASC LIMIT ?", (guild_id, int(limit)))
        return [(int(uid), int(ms), int(td)) for (uid, ms, td) in cur.fetchall()]

__all__ = ['attendance_check_in','attendance_today','attendance_max_streak_leaderboard']


def attendance_yesterday_not_today(guild_id: int):
    """Return user_ids who checked in yesterday but not today (i.e., last_date == yesterday)."""
    yday = _yesterday_kst()
    with get_conn() as conn:
        cur = conn.execute("SELECT user_id FROM attendance WHERE guild_id=? AND last_date=?", (guild_id, yday))
        return [int(u) for (u,) in cur.fetchall()]
