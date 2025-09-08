from .core import get_conn

DEFAULT_BALANCE = 1000


def _ensure_user(conn, user_id: int) -> int:
    cur = conn.execute("SELECT balance FROM balances WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (user_id, DEFAULT_BALANCE))
        return DEFAULT_BALANCE
    return int(row[0])


def get_balance(user_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute("SELECT balance FROM balances WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row is None:
            conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (user_id, DEFAULT_BALANCE))
            return DEFAULT_BALANCE
        return int(row[0])


def transfer(sender_id: int, receiver_id: int, amount: int) -> tuple[int, int]:
    if amount <= 0:
        raise ValueError("Amount must be positive")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        sender_balance = _ensure_user(conn, sender_id)
        receiver_balance = _ensure_user(conn, receiver_id)
        if sender_id == receiver_id:
            raise ValueError("Cannot transfer to self")
        if sender_balance < amount:
            raise ValueError("Insufficient funds")
        new_sender = sender_balance - amount
        new_receiver = receiver_balance + amount
        conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (new_sender, sender_id))
        conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (new_receiver, receiver_id))
        return new_sender, new_receiver


def top_balances(limit: int = 10) -> list[tuple[int, int]]:
    limit = max(1, min(int(limit), 50))
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT user_id, balance FROM balances ORDER BY balance DESC, user_id ASC LIMIT ?",
            (limit,),
        )
        return [(int(uid), int(bal)) for uid, bal in cur.fetchall()]


def get_rank(user_id: int) -> tuple[int, int, int]:
    with get_conn() as conn:
        balance = _ensure_user(conn, user_id)
        cur = conn.execute("SELECT COUNT(DISTINCT balance) FROM balances WHERE balance > ?", (balance,))
        higher = int(cur.fetchone()[0])
        cur = conn.execute("SELECT COUNT(*) FROM balances")
        total = int(cur.fetchone()[0])
        return higher + 1, balance, total


def count_users() -> int:
    with get_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM balances")
        return int(cur.fetchone()[0])


def rank_page(offset: int, limit: int) -> list[tuple[int, int]]:
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 50))
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT user_id, balance FROM balances ORDER BY balance DESC, user_id ASC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [(int(uid), int(bal)) for uid, bal in cur.fetchall()]

__all__ = [
    'DEFAULT_BALANCE', 'get_balance', 'transfer', 'top_balances', 'get_rank', 'count_users', 'rank_page'
]
