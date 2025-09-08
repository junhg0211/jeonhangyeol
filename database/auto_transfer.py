from .core import get_conn


def create_auto_transfer(guild_id: int, from_user: int, to_user: int, amount: int, period_days: int, start_date: str) -> int:
    if amount <= 0:
        raise ValueError("금액은 0보다 커야 합니다.")
    if period_days <= 0 or period_days > 365:
        raise ValueError("주기는 1~365일 범위여야 합니다.")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO auto_transfers(guild_id, from_user, to_user, amount, period_days, start_date) VALUES(?, ?, ?, ?, ?, ?)",
            (guild_id, from_user, to_user, int(amount), int(period_days), start_date),
        )
        return int(cur.lastrowid)


def list_user_auto_transfers(guild_id: int, from_user: int):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, to_user, amount, period_days, start_date, last_date, active FROM auto_transfers WHERE guild_id=? AND from_user=? ORDER BY id DESC",
            (guild_id, from_user),
        )
        return cur.fetchall()


def cancel_auto_transfer(guild_id: int, from_user: int, auto_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE auto_transfers SET active=0 WHERE id=? AND guild_id=? AND from_user=? AND active=1",
            (auto_id, guild_id, from_user),
        )
        return cur.rowcount > 0


def list_due_auto_transfers(today: str):
    # import helpers lazily to avoid cycles
    from .core import KST
    from datetime import datetime

    def _days_between_kst(d1: str, d2: str) -> int:
        a = datetime.strptime(d1, "%Y-%m-%d").replace(tzinfo=KST)
        b = datetime.strptime(d2, "%Y-%m-%d").replace(tzinfo=KST)
        return int((b.date() - a.date()).days)

    with get_conn() as conn:
        cur = conn.execute("SELECT id, guild_id, from_user, to_user, amount, period_days, start_date, last_date FROM auto_transfers WHERE active=1")
        rows = []
        for id_, gid, frm, to, amt, period, start_date, last_date in cur.fetchall():
            if start_date > today:
                continue
            if last_date == today:
                continue
            days = _days_between_kst(start_date, today)
            if days % int(period) == 0:
                rows.append((int(id_), int(gid), int(frm), int(to), int(amt)))
        return rows


def mark_auto_transfer_run(auto_id: int, success: bool, message: str | None, today: str | None = None) -> None:
    import time
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO auto_transfer_logs(ts, auto_id, status, message) VALUES(?, ?, ?, ?)",
            (now, int(auto_id), "OK" if success else "ERR", message or None),
        )
        if success and today:
            conn.execute("UPDATE auto_transfers SET last_date=? WHERE id=?", (today, int(auto_id)))


def list_open_orders_for_guild(guild_id: int):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, user_id, symbol, side, qty, order_type, limit_price FROM orders JOIN (SELECT DISTINCT 1) ON 1=1 WHERE guild_id=? AND status='OPEN' ORDER BY id ASC",
            (guild_id,),
        )
        return cur.fetchall()


__all__ = [
    'create_auto_transfer','list_user_auto_transfers','cancel_auto_transfer',
    'list_due_auto_transfers','mark_auto_transfer_run','list_open_orders_for_guild',
]
