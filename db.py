import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), "data.sqlite3"))


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS balances (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL
            );
            """
        )


DEFAULT_BALANCE = 1000


def get_balance(user_id: int) -> int:
    """Fetch balance; create with default if missing."""
    with get_conn() as conn:
        cur = conn.execute("SELECT balance FROM balances WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO balances(user_id, balance) VALUES(?, ?)",
                (user_id, DEFAULT_BALANCE),
            )
            return DEFAULT_BALANCE
        return int(row[0])


def _ensure_user(conn: sqlite3.Connection, user_id: int) -> int:
    cur = conn.execute("SELECT balance FROM balances WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO balances(user_id, balance) VALUES(?, ?)",
            (user_id, DEFAULT_BALANCE),
        )
        return DEFAULT_BALANCE
    return int(row[0])


def transfer(sender_id: int, receiver_id: int, amount: int) -> tuple[int, int]:
    """Atomically move funds from sender to receiver.

    Returns (sender_balance, receiver_balance) after transfer.
    Raises ValueError on invalid amounts or insufficient funds.
    """
    if amount <= 0:
        raise ValueError("Amount must be positive")

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")  # acquire write lock for atomicity

        sender_balance = _ensure_user(conn, sender_id)
        receiver_balance = _ensure_user(conn, receiver_id)

        if sender_id == receiver_id:
            raise ValueError("Cannot transfer to self")
        if sender_balance < amount:
            raise ValueError("Insufficient funds")

        new_sender = sender_balance - amount
        new_receiver = receiver_balance + amount

        conn.execute(
            "UPDATE balances SET balance=? WHERE user_id=?",
            (new_sender, sender_id),
        )
        conn.execute(
            "UPDATE balances SET balance=? WHERE user_id=?",
            (new_receiver, receiver_id),
        )

        return new_sender, new_receiver

