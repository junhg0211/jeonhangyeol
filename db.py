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
        # 아이템 마스터 테이블 (이모지+이름으로 유니크)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                emoji TEXT NOT NULL,
                UNIQUE(name, emoji)
            );
            """
        )
        # 사용자 인벤토리: 항목별 수량
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                user_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                qty INTEGER NOT NULL,
                PRIMARY KEY (user_id, item_id),
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
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


def top_balances(limit: int = 10) -> list[tuple[int, int]]:
    """Return list of (user_id, balance) ordered by balance desc.

    Args:
        limit: number of rows to return (1-50 safeguard)
    """
    limit = max(1, min(int(limit), 50))
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT user_id, balance FROM balances ORDER BY balance DESC, user_id ASC LIMIT ?",
            (limit,),
        )
        return [(int(uid), int(bal)) for uid, bal in cur.fetchall()]


def get_rank(user_id: int) -> tuple[int, int, int]:
    """Return (rank, balance, total_users) for the given user_id.

    If user does not exist, ensures creation and returns their default rank.
    Rank is 1-based; ties share rank via dense ranking.
    """
    with get_conn() as conn:
        # Ensure user exists
        balance = _ensure_user(conn, user_id)

        # Dense rank: count distinct balances greater than user's balance, then +1
        cur = conn.execute(
            "SELECT COUNT(DISTINCT balance) FROM balances WHERE balance > ?",
            (balance,),
        )
        higher = int(cur.fetchone()[0])
        cur = conn.execute("SELECT COUNT(*) FROM balances")
        total = int(cur.fetchone()[0])
        rank = higher + 1
        return rank, balance, total


# ----------------------
# Inventory / Items APIs
# ----------------------

def _get_or_create_item(conn: sqlite3.Connection, name: str, emoji: str) -> int:
    name = name.strip()
    emoji = emoji.strip()
    if not name or not emoji:
        raise ValueError("Item name and emoji must be non-empty")
    cur = conn.execute(
        "SELECT id FROM items WHERE name=? AND emoji=?",
        (name, emoji),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO items(name, emoji) VALUES(?, ?)",
        (name, emoji),
    )
    return int(cur.lastrowid)


def grant_item(user_id: int, name: str, emoji: str, qty: int = 1) -> int:
    """Give `qty` of item to user. Returns new quantity for that item.

    Creates the item and/or inventory row as needed.
    """
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item_id = _get_or_create_item(conn, name, emoji)
        # upsert
        conn.execute(
            """
            INSERT INTO inventory(user_id, item_id, qty) VALUES(?, ?, ?)
            ON CONFLICT(user_id, item_id)
            DO UPDATE SET qty=qty+excluded.qty
            """,
            (user_id, item_id, qty),
        )
        cur = conn.execute(
            "SELECT qty FROM inventory WHERE user_id=? AND item_id=?",
            (user_id, item_id),
        )
        return int(cur.fetchone()[0])


def transfer_item(sender_id: int, receiver_id: int, name: str, emoji: str, qty: int = 1) -> tuple[int, int]:
    """Atomically transfer `qty` of an item from sender to receiver.

    Returns (sender_qty, receiver_qty) for this item after transfer.
    """
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    if sender_id == receiver_id:
        raise ValueError("Cannot transfer to self")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item_id = _get_or_create_item(conn, name, emoji)

        # ensure sender row exists with default 0
        cur = conn.execute(
            "SELECT qty FROM inventory WHERE user_id=? AND item_id=?",
            (sender_id, item_id),
        )
        row = cur.fetchone()
        sender_qty = int(row[0]) if row else 0
        if sender_qty < qty:
            raise ValueError("Insufficient item quantity")

        # decrement sender
        new_sender_qty = sender_qty - qty
        if row:
            if new_sender_qty == 0:
                conn.execute(
                    "DELETE FROM inventory WHERE user_id=? AND item_id=?",
                    (sender_id, item_id),
                )
            else:
                conn.execute(
                    "UPDATE inventory SET qty=? WHERE user_id=? AND item_id=?",
                    (new_sender_qty, sender_id, item_id),
                )

        # increment receiver
        conn.execute(
            """
            INSERT INTO inventory(user_id, item_id, qty) VALUES(?, ?, ?)
            ON CONFLICT(user_id, item_id)
            DO UPDATE SET qty=qty+excluded.qty
            """,
            (receiver_id, item_id, qty),
        )
        cur = conn.execute(
            "SELECT qty FROM inventory WHERE user_id=? AND item_id=?",
            (receiver_id, item_id),
        )
        receiver_qty = int(cur.fetchone()[0])

        return new_sender_qty, receiver_qty


def list_inventory(user_id: int, query: str | None = None) -> list[tuple[str, str, int]]:
    """Return list of (emoji, name, qty) for user's inventory, qty desc then name.
    If `query` is provided, filter by name or emoji containing the substring (case-insensitive for name).
    """
    with get_conn() as conn:
        if query:
            q = f"%{query.lower()}%"
            cur = conn.execute(
                """
                SELECT i.emoji, i.name, inv.qty
                FROM inventory AS inv
                JOIN items AS i ON i.id = inv.item_id
                WHERE inv.user_id = ?
                  AND (LOWER(i.name) LIKE ? OR i.emoji LIKE ?)
                ORDER BY inv.qty DESC, i.name ASC
                """,
                (user_id, q, q),
            )
        else:
            cur = conn.execute(
                """
                SELECT i.emoji, i.name, inv.qty
                FROM inventory AS inv
                JOIN items AS i ON i.id = inv.item_id
                WHERE inv.user_id = ?
                ORDER BY inv.qty DESC, i.name ASC
                """,
                (user_id,),
            )
        return [(str(emoji), str(name), int(qty)) for (emoji, name, qty) in cur.fetchall()]
