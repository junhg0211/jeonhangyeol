from .core import get_conn
from .economy import DEFAULT_BALANCE, _ensure_user
import time


def create_auction(seller_id: int, name: str, emoji: str, qty: int, start_price: int, duration_seconds: int, guild_id: int | None = None) -> int:
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    if start_price < 0:
        raise ValueError("Start price must be >= 0")
    if duration_seconds < 3600 or duration_seconds > 30 * 24 * 3600:
        raise ValueError("Duration must be between 1 hour and 30 days")
    now = int(time.time())
    end_at = now + duration_seconds
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # decrement seller inventory
        cur = conn.execute("SELECT id FROM items WHERE name=? AND emoji=?", (name.strip(), emoji.strip()))
        row = cur.fetchone()
        if not row:
            raise ValueError("Item not found in catalog")
        item_id = int(row[0])
        cur = conn.execute("SELECT qty FROM inventory WHERE user_id=? AND item_id=?", (seller_id, item_id))
        row = cur.fetchone()
        have = int(row[0]) if row else 0
        if have < qty:
            raise ValueError("Insufficient item quantity")
        new_qty = have - qty
        if new_qty == 0:
            conn.execute("DELETE FROM inventory WHERE user_id=? AND item_id=?", (seller_id, item_id))
        else:
            conn.execute("UPDATE inventory SET qty=? WHERE user_id=? AND item_id=?", (new_qty, seller_id, item_id))

        cur = conn.execute(
            """
            INSERT INTO auctions (seller_id, name, emoji, qty, start_price, created_at, end_at, status, guild_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (seller_id, name.strip(), emoji.strip(), qty, start_price, now, end_at, guild_id),
        )
        return int(cur.lastrowid)


def get_auction(auction_id: int):
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM auctions WHERE id=?", (auction_id,))
        return cur.fetchone()


def list_open_auctions(offset: int, limit: int, query: str | None = None, guild_id: int | None = None):
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))
    now = int(time.time())
    with get_conn() as conn:
        base = "SELECT id, seller_id, name, emoji, qty, start_price, current_bid, current_bidder_id, end_at FROM auctions WHERE status='open' AND end_at > ?"
        args = [now]
        if guild_id is not None:
            base += " AND guild_id = ?"
            args.append(guild_id)
        if query:
            base += " AND (LOWER(name) LIKE ? OR emoji LIKE ?)"
            q = f"%{query.lower()}%"
            args.extend([q, q])
        base += " ORDER BY end_at ASC LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        cur = conn.execute(base, tuple(args))
        return cur.fetchall()


def count_open_auctions(query: str | None = None, guild_id: int | None = None) -> int:
    now = int(time.time())
    with get_conn() as conn:
        base = "SELECT COUNT(*) FROM auctions WHERE status='open' AND end_at > ?"
        args = [now]
        if guild_id is not None:
            base += " AND guild_id = ?"
            args.append(guild_id)
        if query:
            base += " AND (LOWER(name) LIKE ? OR emoji LIKE ?)"
            q = f"%{query.lower()}%"
            args.extend([q, q])
        cur = conn.execute(base, tuple(args))
        return int(cur.fetchone()[0])


def place_bid(auction_id: int, bidder_id: int, amount: int):
    if amount <= 0:
        raise ValueError("Bid must be positive")
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "SELECT seller_id, start_price, current_bid, current_bidder_id, end_at, status, name, emoji, qty FROM auctions WHERE id=?",
            (auction_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Auction not found")
        seller_id, start_price, current_bid, current_bidder_id, end_at, status, name, emoji, qty = row
        if status != 'open' or end_at <= now:
            raise ValueError("Auction is closed")
        if bidder_id == seller_id:
            raise ValueError("Seller cannot bid on own auction")
        min_required = current_bid if current_bid is not None else start_price
        if amount <= (min_required if min_required is not None else 0):
            raise ValueError("Bid must be higher than current price")

        # deduct bidder funds
        bal = _ensure_user(conn, bidder_id)
        if bal < amount:
            raise ValueError("Insufficient funds")
        conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (bal - amount, bidder_id))
        # refund previous top bidder
        prev_id = current_bidder_id
        prev_amt = int(current_bid) if current_bid is not None else None
        if current_bidder_id is not None and current_bid is not None:
            cur = conn.execute("SELECT balance FROM balances WHERE user_id=?", (current_bidder_id,))
            rowp = cur.fetchone()
            prev_bal = int(rowp[0]) if rowp else DEFAULT_BALANCE
            if rowp is None:
                conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (current_bidder_id, prev_bal))
            conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (prev_bal + int(current_bid), current_bidder_id))

        conn.execute("UPDATE auctions SET current_bid=?, current_bidder_id=? WHERE id=?", (amount, bidder_id, auction_id))
        conn.execute("INSERT INTO auction_bids(auction_id, bidder_id, amount, created_at) VALUES(?, ?, ?, ?)", (auction_id, bidder_id, amount, now))
        return amount, bidder_id, (int(prev_id) if prev_id is not None else None), (int(prev_amt) if prev_amt is not None else None)


def finalize_due_auctions(max_to_close: int = 50) -> int:
    now = int(time.time())
    closed = 0
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, guild_id, seller_id, name, emoji, qty, current_bid, current_bidder_id FROM auctions WHERE status='open' AND end_at <= ? LIMIT ?",
            (now, max_to_close),
        )
        rows = cur.fetchall()
        for (aid, gid, seller_id, name, emoji, qty, current_bid, current_bidder_id) in rows:
            conn.execute("SAVEPOINT fin_one")
            try:
                cur2 = conn.execute("SELECT status, current_bid, current_bidder_id FROM auctions WHERE id=?", (aid,))
                row2 = cur2.fetchone()
                if not row2:
                    conn.execute("RELEASE fin_one")
                    continue
                st, cb, cbid = row2
                if st != 'open':
                    conn.execute("RELEASE fin_one")
                    continue
                if cbid is None or cb is None:
                    conn.execute(
                        """
                        INSERT INTO inventory(user_id, item_id, qty)
                        SELECT ?, i.id, ? FROM items i WHERE i.name=? AND i.emoji=?
                        ON CONFLICT(user_id, item_id) DO UPDATE SET qty=qty+excluded.qty
                        """,
                        (seller_id, qty, name, emoji),
                    )
                    conn.execute("UPDATE auctions SET status='closed', winner_id=NULL, winning_bid=NULL WHERE id=?", (aid,))
                    closed += 1
                    conn.execute("RELEASE fin_one")
                    continue
                # winner case
                conn.execute(
                    """
                    INSERT INTO inventory(user_id, item_id, qty)
                    SELECT ?, i.id, ? FROM items i WHERE i.name=? AND i.emoji=?
                    ON CONFLICT(user_id, item_id) DO UPDATE SET qty=qty+excluded.qty
                    """,
                    (cbid, qty, name, emoji),
                )
                # patent ownership transfer
                try:
                    if str(emoji) == "📜" and str(name).startswith("특허:"):
                        w = str(name).split(":", 1)[1]
                        conn.execute("UPDATE patents SET owner_id=? WHERE guild_id=? AND word=?", (cbid, int(gid) if gid is not None else 0, w))
                except Exception:
                    pass
                # pay seller
                cur3 = conn.execute("SELECT balance FROM balances WHERE user_id=?", (seller_id,))
                rowb = cur3.fetchone()
                seller_bal = int(rowb[0]) if rowb else DEFAULT_BALANCE
                if rowb is None:
                    conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (seller_id, seller_bal))
                conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (seller_bal + int(cb), seller_id))
                conn.execute("UPDATE auctions SET status='closed', winner_id=?, winning_bid=? WHERE id=?", (cbid, cb, aid))
                closed += 1
                conn.execute("RELEASE fin_one")
            except Exception:
                conn.execute("ROLLBACK TO fin_one")
                conn.execute("RELEASE fin_one")
                continue
    return closed


def finalize_due_auctions_details(max_to_close: int = 50):
    now = int(time.time())
    results = []
    with get_conn() as conn:
        cur = conn.execute("SELECT id, guild_id, seller_id, name, emoji, qty, current_bid, current_bidder_id FROM auctions WHERE status='open' AND end_at <= ? LIMIT ?", (now, max_to_close))
        for (aid, gid, seller_id, name, emoji, qty, current_bid, current_bidder_id) in cur.fetchall():
            conn.execute("SAVEPOINT fin_det_one")
            try:
                row2 = conn.execute("SELECT status, current_bid, current_bidder_id FROM auctions WHERE id=?", (aid,)).fetchone()
                if not row2:
                    conn.execute("RELEASE fin_det_one")
                    continue
                st, cb, cbid = row2
                if st != 'open':
                    conn.execute("RELEASE fin_det_one")
                    continue
                if cbid is None or cb is None:
                    conn.execute(
                        """
                        INSERT INTO inventory(user_id, item_id, qty)
                        SELECT ?, i.id, ? FROM items i WHERE i.name=? AND i.emoji=?
                        ON CONFLICT(user_id, item_id) DO UPDATE SET qty=qty+excluded.qty
                        """,
                        (seller_id, qty, name, emoji),
                    )
                    conn.execute("UPDATE auctions SET status='closed', winner_id=NULL, winning_bid=NULL WHERE id=?", (aid,))
                    results.append({'id': aid, 'guild_id': int(gid) if gid is not None else None, 'seller_id': seller_id, 'name': name, 'emoji': emoji, 'qty': qty, 'status': 'unsold_return'})
                    conn.execute("RELEASE fin_det_one")
                    continue
                conn.execute(
                    """
                    INSERT INTO inventory(user_id, item_id, qty)
                    SELECT ?, i.id, ? FROM items i WHERE i.name=? AND i.emoji=?
                    ON CONFLICT(user_id, item_id) DO UPDATE SET qty=qty+excluded.qty
                    """,
                    (cbid, qty, name, emoji),
                )
                try:
                    if str(emoji) == "📜" and str(name).startswith("특허:"):
                        w = str(name).split(":", 1)[1]
                        conn.execute("UPDATE patents SET owner_id=? WHERE guild_id=? AND word=?", (cbid, int(gid) if gid is not None else 0, w))
                except Exception:
                    pass
                cur3 = conn.execute("SELECT balance FROM balances WHERE user_id=?", (seller_id,))
                rowb = cur3.fetchone()
                seller_bal = int(rowb[0]) if rowb else DEFAULT_BALANCE
                if rowb is None:
                    conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (seller_id, seller_bal))
                conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (seller_bal + int(cb), seller_id))
                conn.execute("UPDATE auctions SET status='closed', winner_id=?, winning_bid=? WHERE id=?", (cbid, cb, aid))
                results.append({'id': aid, 'guild_id': int(gid) if gid is not None else None, 'seller_id': seller_id, 'name': name, 'emoji': emoji, 'qty': qty, 'status': 'sold', 'winner_id': cbid, 'winning_bid': cb})
                conn.execute("RELEASE fin_det_one")
            except Exception:
                conn.execute("ROLLBACK TO fin_det_one")
                conn.execute("RELEASE fin_det_one")
                continue
    return results


def list_due_unsold_auctions(limit: int = 50):
    now = int(time.time())
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, guild_id, seller_id, name, emoji, qty
            FROM auctions
            WHERE status='open' AND end_at <= ? AND current_bid IS NULL AND current_bidder_id IS NULL
            LIMIT ?
            """,
            (now, limit),
        )
        return cur.fetchall()


def discard_unsold_auction(aid: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE auctions SET status='closed', winner_id=NULL, winning_bid=NULL WHERE id=?", (aid,))


def get_auction_guild(aid: int) -> tuple[int | None, int, str]:
    with get_conn() as conn:
        cur = conn.execute("SELECT guild_id, end_at, status FROM auctions WHERE id=?", (aid,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Auction not found")
        gid, end_at, status = row
        return (int(gid) if gid is not None else None, int(end_at), str(status))

__all__ = [
    'create_auction','get_auction','list_open_auctions','count_open_auctions','place_bid',
    'finalize_due_auctions','finalize_due_auctions_details','list_due_unsold_auctions','discard_unsold_auction','get_auction_guild'
]
