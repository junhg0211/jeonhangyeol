from .core import get_conn
from typing import List, Tuple

INSTRUMENT_ITEM_MAP = {
    "IDX_CHAT": ("📈", "IDX_CHAT"),
    "IDX_VOICE": ("🗣️", "IDX_VOICE"),
    "IDX_REACT": ("✨", "IDX_REACT"),
    "ETF_ALL": ("📊", "ETF_ALL"),
}


def instrument_item(symbol: str) -> tuple[str, str]:
    s = (symbol or "").upper()
    if s in ("ETF_CHAT", "ETF_VOICE", "ETF_REACT"):
        s = s.replace("ETF_", "IDX_")
    if s == "IDX_ALL":
        s = "ETF_ALL"
    if s not in INSTRUMENT_ITEM_MAP:
        raise ValueError("Unknown symbol")
    return INSTRUMENT_ITEM_MAP[s]


def instrument_item_names() -> set[str]:
    return {name for (_sym, (_emo, name)) in INSTRUMENT_ITEM_MAP.items()}


def is_instrument_item_name(name: str) -> bool:
    return name in instrument_item_names()


def is_patent_item_name(name: str) -> bool:
    return isinstance(name, str) and name.startswith("특허:")


def list_inventory(user_id: int, query: str | None = None) -> List[Tuple[str, str, int]]:
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


def _get_or_create_item(conn, name: str, emoji: str) -> int:
    cur = conn.execute("SELECT id FROM items WHERE name=? AND emoji=?", (name, emoji))
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur = conn.execute("INSERT INTO items(name, emoji) VALUES(?, ?)", (name, emoji))
    return int(cur.lastrowid)


def grant_item(user_id: int, name: str, emoji: str, qty: int = 1) -> int:
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item_id = _get_or_create_item(conn, name.strip(), emoji.strip())
        conn.execute(
            """
            INSERT INTO inventory(user_id, item_id, qty) VALUES(?, ?, ?)
            ON CONFLICT(user_id, item_id) DO UPDATE SET qty=qty+excluded.qty
            """,
            (user_id, item_id, qty),
        )
        cur = conn.execute("SELECT qty FROM inventory WHERE user_id=? AND item_id=?", (user_id, item_id))
        return int(cur.fetchone()[0])


def discard_item(user_id: int, name: str, emoji: str, qty: int = 1) -> int:
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("SELECT id FROM items WHERE name=? AND emoji=?", (name.strip(), emoji.strip()))
        row = cur.fetchone()
        if not row:
            raise ValueError("Item not found")
        item_id = int(row[0])
        cur = conn.execute("SELECT qty FROM inventory WHERE user_id=? AND item_id=?", (user_id, item_id))
        row = cur.fetchone()
        current = int(row[0]) if row else 0
        if current < qty:
            raise ValueError("Insufficient item quantity")
        new_qty = current - qty
        if new_qty == 0:
            conn.execute("DELETE FROM inventory WHERE user_id=? AND item_id=?", (user_id, item_id))
        else:
            conn.execute("UPDATE inventory SET qty=? WHERE user_id=? AND item_id=?", (new_qty, user_id, item_id))
        return new_qty


def transfer_item(sender_id: int, receiver_id: int, name: str, emoji: str, qty: int = 1) -> tuple[int, int]:
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    if sender_id == receiver_id:
        raise ValueError("Cannot transfer to self")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("SELECT id FROM items WHERE name=? AND emoji=?", (name, emoji))
        row = cur.fetchone()
        if not row:
            # create to keep IDs consistent
            item_id = _get_or_create_item(conn, name, emoji)
        else:
            item_id = int(row[0])
        cur = conn.execute("SELECT qty FROM inventory WHERE user_id=? AND item_id=?", (sender_id, item_id))
        row = cur.fetchone()
        sender_qty = int(row[0]) if row else 0
        if sender_qty < qty:
            raise ValueError("Insufficient item quantity")
        new_sender = sender_qty - qty
        if new_sender == 0:
            conn.execute("DELETE FROM inventory WHERE user_id=? AND item_id=?", (sender_id, item_id))
        else:
            conn.execute("UPDATE inventory SET qty=? WHERE user_id=? AND item_id=?", (new_sender, sender_id, item_id))
        conn.execute(
            """
            INSERT INTO inventory(user_id, item_id, qty) VALUES(?, ?, ?)
            ON CONFLICT(user_id, item_id) DO UPDATE SET qty=qty+excluded.qty
            """,
            (receiver_id, item_id, qty),
        )
        cur = conn.execute("SELECT qty FROM inventory WHERE user_id=? AND item_id=?", (receiver_id, item_id))
        receiver_qty = int(cur.fetchone()[0])
        return new_sender, receiver_qty

__all__ = [
    'list_inventory','grant_item','discard_item','transfer_item','instrument_item','INSTRUMENT_ITEM_MAP',
    'instrument_item_names','is_instrument_item_name','is_patent_item_name'
]

__all__ = [
    'list_inventory',
    'grant_item',
    'discard_item',
    'transfer_item',
    'instrument_item',
    'INSTRUMENT_ITEM_MAP',
    'instrument_item_names',
    'is_instrument_item_name',
    'is_patent_item_name',
]


def list_items_for_users(user_ids: List[int]) -> List[Tuple[str, str, int, int]]:
    """Aggregate items held by the given users.

    Returns list of (emoji, name, total_qty, holders_count), ordered by total_qty desc, name asc.
    """
    if not user_ids:
        return []
    res: list[tuple[str, str, int, int]] = []
    # SQLite has a limit on variables (~999); chunk the IN list
    chunk = 800
    parts: list[tuple[str, str, int, int]] = []
    with get_conn() as conn:
        for i in range(0, len(user_ids), chunk):
            ids = [int(x) for x in user_ids[i:i+chunk]]
            q = ",".join(["?"] * len(ids))
            cur = conn.execute(
                f"""
                SELECT i.emoji, i.name, SUM(inv.qty) AS total_qty, COUNT(DISTINCT inv.user_id) AS holders
                FROM inventory AS inv
                JOIN items AS i ON i.id = inv.item_id
                WHERE inv.user_id IN ({q})
                GROUP BY i.emoji, i.name
                """,
                tuple(ids),
            )
            parts.extend([(str(e), str(n), int(t), int(h)) for (e, n, t, h) in cur.fetchall()])
    # merge parts (same item may appear in different chunks)
    agg: dict[tuple[str, str], tuple[int, set[int]]] = {}
    # To reconstruct holders correctly, run a second pass to count distinct users per item.
    # Simpler approach: query again per item for holders. But that's N queries. Instead, do a single join without chunking for holders when len<=999.
    # For simplicity and performance balance, we will merge totals from parts and re-query holders if needed when chunks>1.
    totals: dict[tuple[str, str], int] = {}
    for e, n, t, h in parts:
        key = (e, n)
        totals[key] = totals.get(key, 0) + t
    # Holders count approximate from parts is not accurate; recompute with a single pass when possible.
    holders: dict[tuple[str, str], int] = {}
    if len(user_ids) <= 999:
        q = ",".join(["?"] * len(user_ids))
        with get_conn() as conn:
            cur = conn.execute(
                f"""
                SELECT i.emoji, i.name, COUNT(DISTINCT inv.user_id) AS holders
                FROM inventory AS inv
                JOIN items AS i ON i.id = inv.item_id
                WHERE inv.user_id IN ({q})
                GROUP BY i.emoji, i.name
                """,
                tuple(int(x) for x in user_ids),
            )
            for e, n, h in cur.fetchall():
                holders[(str(e), str(n))] = int(h)
    else:
        # Fallback: approximate by summing distinct counts from chunks (may overcount if same user appears in multiple chunks, which doesn't happen).
        # Since chunks are disjoint, this is exact.
        # We didn't collect per-item holders per chunk, so approximate holders = number of users that hold the item by re-running per item queries could be heavy.
        # As a compromise, compute holders using an EXISTS subquery per item key.
        keys = list(totals.keys())
        with get_conn() as conn:
            for (e, n) in keys:
                cnt = 0
                for i in range(0, len(user_ids), chunk):
                    ids = [int(x) for x in user_ids[i:i+chunk]]
                    q = ",".join(["?"] * len(ids))
                    cur = conn.execute(
                        f"""
                        SELECT COUNT(DISTINCT inv.user_id)
                        FROM inventory AS inv
                        JOIN items AS it ON it.id=inv.item_id
                        WHERE it.emoji=? AND it.name=? AND inv.user_id IN ({q})
                        """,
                        (e, n, *ids),
                    )
                    row = cur.fetchone()
                    cnt += int(row[0]) if row else 0
                holders[(e, n)] = cnt
    for key, total in totals.items():
        e, n = key
        h = holders.get(key, 0)
        res.append((e, n, total, h))
    res.sort(key=lambda x: (-x[2], x[1]))
    return res
