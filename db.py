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
        # ETF 분봉 기록
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_ticks (
                guild_id INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                delta REAL NOT NULL,
                PRIMARY KEY (guild_id, ts, symbol),
                FOREIGN KEY (symbol) REFERENCES instruments(symbol) ON DELETE CASCADE
            );
            """
        )
        # 투자: 상품, 포지션, 체결 내역
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instruments (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,   -- ETF/INDEX
                category TEXT         -- chat/voice/react/all
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                qty INTEGER NOT NULL,
                avg_cost REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id, symbol),
                FOREIGN KEY (symbol) REFERENCES instruments(symbol) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,   -- BUY/SELL
                qty INTEGER NOT NULL,
                price REAL NOT NULL,
                notional INTEGER NOT NULL,
                FOREIGN KEY (symbol) REFERENCES instruments(symbol) ON DELETE CASCADE
            );
            """
        )
        # 주문(예약)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_ts INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,          -- BUY/SELL
                qty INTEGER NOT NULL,
                order_type TEXT NOT NULL,    -- MARKET_OPEN/LIMIT
                limit_price REAL,
                status TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN/FILLED/CANCELLED
                executed_ts INTEGER,
                executed_price REAL,
                note TEXT,
                FOREIGN KEY (symbol) REFERENCES instruments(symbol) ON DELETE CASCADE
            );
            """
        )
        # 활동 지수: 일자별 지수 상태
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_indices (
                guild_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                open_idx REAL NOT NULL,
                current_idx REAL NOT NULL,
                lower_bound REAL NOT NULL,
                upper_bound REAL NOT NULL,
                opened_at INTEGER NOT NULL,
                closed_at INTEGER,
                high_idx REAL,
                low_idx REAL,
                PRIMARY KEY (guild_id, date, category)
            );
            """
        )
        # Migration for older rows
        try:
            conn.execute("ALTER TABLE activity_indices ADD COLUMN high_idx REAL")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE activity_indices ADD COLUMN low_idx REAL")
        except Exception:
            pass
        # 분단위 변동 기록
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_ticks (
                guild_id INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                idx_value REAL NOT NULL,
                delta REAL NOT NULL,
                chat_count INTEGER NOT NULL,
                react_count INTEGER NOT NULL,
                voice_count INTEGER NOT NULL,
                PRIMARY KEY (guild_id, ts, category)
            );
            """
        )
        # 길드 설정: 경매 알림 채널 등
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                auction_channel_id INTEGER
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
        # 경매 테이블
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auctions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                emoji TEXT NOT NULL,
                qty INTEGER NOT NULL,
                start_price INTEGER NOT NULL,
                current_bid INTEGER,
                current_bidder_id INTEGER,
                created_at INTEGER NOT NULL,
                end_at INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                winner_id INTEGER,
                winning_bid INTEGER
            );
            """
        )
        # schema migration: add guild_id column if missing
        try:
            conn.execute("ALTER TABLE auctions ADD COLUMN guild_id INTEGER")
        except Exception:
            pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auction_bids (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                auction_id INTEGER NOT NULL,
                bidder_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (auction_id) REFERENCES auctions(id) ON DELETE CASCADE
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


def count_users() -> int:
    """Return total number of users with a balance row."""
    with get_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM balances")
        return int(cur.fetchone()[0])


def rank_page(offset: int, limit: int) -> list[tuple[int, int]]:
    """Return a page of (user_id, balance) ordered by balance desc then user_id asc."""
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 50))
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT user_id, balance FROM balances ORDER BY balance DESC, user_id ASC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [(int(uid), int(bal)) for uid, bal in cur.fetchall()]


# ----------------------
# Guild settings APIs
# ----------------------

def set_auction_channel(guild_id: int, channel_id: int | None) -> None:
    with get_conn() as conn:
        if channel_id is None:
            conn.execute(
                "INSERT INTO guild_settings(guild_id, auction_channel_id) VALUES(?, NULL)\n                 ON CONFLICT(guild_id) DO UPDATE SET auction_channel_id=NULL",
                (guild_id,),
            )
        else:
            conn.execute(
                "INSERT INTO guild_settings(guild_id, auction_channel_id) VALUES(?, ?)\n                 ON CONFLICT(guild_id) DO UPDATE SET auction_channel_id=excluded.auction_channel_id",
                (guild_id, channel_id),
            )


def get_auction_channel(guild_id: int) -> int | None:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT auction_channel_id FROM guild_settings WHERE guild_id=?",
            (guild_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return int(row[0]) if row[0] is not None else None

# Back-compat alias: treat auction_channel as generic notify channel
def set_notify_channel(guild_id: int, channel_id: int | None) -> None:
    return set_auction_channel(guild_id, channel_id)

def get_notify_channel(guild_id: int) -> int | None:
    return get_auction_channel(guild_id)


# ----------------------
# Activity index APIs
# ----------------------
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def _today_kst(dt_utc: float | None = None) -> str:
    dt = datetime.fromtimestamp(dt_utc, KST) if dt_utc is not None else datetime.now(KST)
    return dt.strftime("%Y-%m-%d")


def ensure_indices_for_day(guild_id: int, date_kst: str | None = None) -> None:
    """Ensure activity indices exist for categories on the given KST date.
    If missing, open with previous close (if any) else 100.
    Bounds: [50%, 200%] of open.
    """
    date_kst = date_kst or _today_kst()
    cats = ("chat", "voice", "react")
    now = int(_time.time())
    with get_conn() as conn:
        for c in cats:
            cur = conn.execute(
                "SELECT 1 FROM activity_indices WHERE guild_id=? AND date=? AND category=?",
                (guild_id, date_kst, c),
            )
            if cur.fetchone():
                continue
            # previous close
            curp = conn.execute(
                "SELECT current_idx FROM activity_indices WHERE guild_id=? AND category=? ORDER BY date DESC LIMIT 1",
                (guild_id, c),
            )
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


def update_activity_tick(
    guild_id: int,
    ts: int,
    category: str,
    idx_value: float,
    delta: float,
    chat_count: int,
    react_count: int,
    voice_count: int,
    date_kst: str | None = None,
) -> None:
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
        cur = conn.execute(
            "SELECT open_idx, lower_bound, upper_bound, current_idx FROM activity_indices WHERE guild_id=? AND date=? AND category=?",
            (guild_id, date_kst, category),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Index not initialised")
        open_idx, lower, upper, current = float(row[0]), float(row[1]), float(row[2]), float(row[3])
        return current, lower, upper


def get_index_info(guild_id: int, date_kst: str, category: str) -> tuple[float, float, float, float | None, float | None, float]:
    """Return (current, lower, upper, high, low, open) for the index."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT current_idx, lower_bound, upper_bound, high_idx, low_idx, open_idx FROM activity_indices WHERE guild_id=? AND date=? AND category=?",
            (guild_id, date_kst, category),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Index not initialised")
        current, lower, upper, high, low, open_idx = row
        return float(current), float(lower), float(upper), (float(high) if high is not None else None), (float(low) if low is not None else None), float(open_idx)


# ----------------------
# Trading helpers
# ----------------------

INSTRUMENTS_DEFAULT = [
    ("ETF_CHAT", "채팅 ETF", "ETF", "chat"),
    ("ETF_VOICE", "통화 ETF", "ETF", "voice"),
    ("ETF_REACT", "반응 ETF", "ETF", "react"),
    ("ETF_ALL", "종합 ETF", "ETF", "all"),
]


def ensure_instruments():
    with get_conn() as conn:
        for sym, name, kind, cat in INSTRUMENTS_DEFAULT:
            conn.execute(
                "INSERT OR IGNORE INTO instruments(symbol, name, kind, category) VALUES(?, ?, ?, ?)",
                (sym, name, kind, cat),
            )


def is_trading_time_kst(ts: int | None = None) -> bool:
    dt = datetime.fromtimestamp(ts, KST) if ts else datetime.now(KST)
    t = dt.time()
    return (t >= datetime.strptime("09:00", "%H:%M").time()) and (t < datetime.strptime("21:00", "%H:%M").time())


def get_symbol_price(guild_id: int, symbol: str, ts: int | None = None) -> float:
    date_kst = _today_kst(ts)
    if symbol == "ETF_CHAT":
        cur, _, _ = get_index_bounds(guild_id, date_kst, "chat")
        return cur
    if symbol == "ETF_VOICE":
        cur, _, _ = get_index_bounds(guild_id, date_kst, "voice")
        return cur
    if symbol == "ETF_REACT":
        cur, _, _ = get_index_bounds(guild_id, date_kst, "react")
        return cur
    if symbol == "ETF_ALL":
        c1, _, _ = get_index_bounds(guild_id, date_kst, "chat")
        c2, _, _ = get_index_bounds(guild_id, date_kst, "voice")
        c3, _, _ = get_index_bounds(guild_id, date_kst, "react")
        return (c1 + c2 + c3) / 3.0
    raise ValueError("Unknown symbol")


def trade_buy(guild_id: int, user_id: int, symbol: str, qty: int) -> tuple[int, float, int, int]:
    """Execute market buy. Returns (new_qty, price, notional, new_balance)."""
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    if not is_trading_time_kst():
        raise ValueError("시장 시간이 아닙니다. (KST 09:00–21:00)")
    price = float(get_symbol_price(guild_id, symbol))
    notional = int(round(price * qty))
    ts = int(_time.time())
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        bal = _ensure_user(conn, user_id)
        if bal < notional:
            raise ValueError("잔액이 부족합니다.")
        # deduct cash
        conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (bal - notional, user_id))
        # position upsert and avg cost
        cur = conn.execute(
            "SELECT qty, avg_cost FROM positions WHERE guild_id=? AND user_id=? AND symbol=?",
            (guild_id, user_id, symbol),
        )
        row = cur.fetchone()
        if row:
            old_qty, avg_cost = int(row[0]), float(row[1])
            new_qty = old_qty + qty
            new_avg = ((avg_cost * old_qty) + (price * qty)) / new_qty
            conn.execute(
                "UPDATE positions SET qty=?, avg_cost=? WHERE guild_id=? AND user_id=? AND symbol=?",
                (new_qty, new_avg, guild_id, user_id, symbol),
            )
        else:
            new_qty = qty
            conn.execute(
                "INSERT INTO positions(guild_id, user_id, symbol, qty, avg_cost) VALUES(?, ?, ?, ?, ?)",
                (guild_id, user_id, symbol, qty, price),
            )
        conn.execute(
            "INSERT INTO trades(ts, guild_id, user_id, symbol, side, qty, price, notional) VALUES(?, ?, ?, ?, 'BUY', ?, ?, ?)",
            (ts, guild_id, user_id, symbol, qty, price, notional),
        )
        # new balance
        new_bal = bal - notional
        return new_qty, price, notional, new_bal


def trade_sell(guild_id: int, user_id: int, symbol: str, qty: int) -> tuple[int, float, int, int]:
    """Execute market sell. Returns (remaining_qty, price, proceeds, new_balance)."""
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    if not is_trading_time_kst():
        raise ValueError("시장 시간이 아닙니다. (KST 09:00–21:00)")
    price = float(get_symbol_price(guild_id, symbol))
    proceeds = int(round(price * qty))
    ts = int(_time.time())
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "SELECT qty FROM positions WHERE guild_id=? AND user_id=? AND symbol=?",
            (guild_id, user_id, symbol),
        )
        row = cur.fetchone()
        pos_qty = int(row[0]) if row else 0
        if pos_qty < qty:
            raise ValueError("보유 수량이 부족합니다.")
        new_qty = pos_qty - qty
        if new_qty == 0:
            conn.execute("DELETE FROM positions WHERE guild_id=? AND user_id=? AND symbol=?", (guild_id, user_id, symbol))
        else:
            conn.execute(
                "UPDATE positions SET qty=? WHERE guild_id=? AND user_id=? AND symbol=?",
                (new_qty, guild_id, user_id, symbol),
            )
        # credit cash
        bal = _ensure_user(conn, user_id)
        new_bal = bal + proceeds
        conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (new_bal, user_id))
        conn.execute(
            "INSERT INTO trades(ts, guild_id, user_id, symbol, side, qty, price, notional) VALUES(?, ?, ?, ?, 'SELL', ?, ?, ?)",
            (ts, guild_id, user_id, symbol, qty, price, proceeds),
        )
        return new_qty, price, proceeds, new_bal


def list_positions(guild_id: int, user_id: int):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT symbol, qty, avg_cost FROM positions WHERE guild_id=? AND user_id=? ORDER BY symbol",
            (guild_id, user_id),
        )
        return [(str(s), int(q), float(a)) for (s, q, a) in cur.fetchall()]


def get_last_etf_price(guild_id: int, symbol: str) -> float | None:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT price FROM etf_ticks WHERE guild_id=? AND symbol=? ORDER BY ts DESC LIMIT 1",
            (guild_id, symbol),
        )
        row = cur.fetchone()
        return float(row[0]) if row else None


def record_etf_tick(guild_id: int, ts: int, symbol: str, price: float, delta: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO etf_ticks(guild_id, ts, symbol, price, delta) VALUES(?, ?, ?, ?, ?)",
            (guild_id, ts, symbol, float(price), float(delta)),
        )


def get_etf_ticks_since(guild_id: int, symbol: str, since_ts: int):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT ts, price FROM etf_ticks WHERE guild_id=? AND symbol=? AND ts>=? ORDER BY ts ASC",
            (guild_id, symbol, since_ts),
        )
        return [(int(ts), float(px)) for (ts, px) in cur.fetchall()]


# ----------------------
# Orders API
# ----------------------

def create_order_market_open(guild_id: int, user_id: int, symbol: str, side: str, qty: int) -> int:
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    ts = int(_time.time())
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders(created_ts, guild_id, user_id, symbol, side, qty, order_type)
            VALUES(?, ?, ?, ?, ?, ?, 'MARKET_OPEN')
            """,
            (ts, guild_id, user_id, symbol, side, qty),
        )
        return int(cur.lastrowid)


def create_order_limit(guild_id: int, user_id: int, symbol: str, side: str, qty: int, limit_price: float) -> int:
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    if limit_price <= 0:
        raise ValueError("Limit price must be positive")
    ts = int(_time.time())
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders(created_ts, guild_id, user_id, symbol, side, qty, order_type, limit_price)
            VALUES(?, ?, ?, ?, ?, ?, 'LIMIT', ?)
            """,
            (ts, guild_id, user_id, symbol, side, qty, float(limit_price)),
        )
        return int(cur.lastrowid)


def list_user_orders(guild_id: int, user_id: int, status: str | None = 'OPEN'):
    with get_conn() as conn:
        if status:
            cur = conn.execute(
                "SELECT id, symbol, side, qty, order_type, limit_price, status, created_ts FROM orders WHERE guild_id=? AND user_id=? AND status=? ORDER BY id DESC",
                (guild_id, user_id, status),
            )
        else:
            cur = conn.execute(
                "SELECT id, symbol, side, qty, order_type, limit_price, status, created_ts FROM orders WHERE guild_id=? AND user_id=? ORDER BY id DESC",
                (guild_id, user_id),
            )
        return cur.fetchall()


def list_open_orders_for_guild(guild_id: int):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, user_id, symbol, side, qty, order_type, limit_price FROM orders WHERE guild_id=? AND status='OPEN' ORDER BY id ASC",
            (guild_id,),
        )
        return cur.fetchall()


def mark_order_filled(order_id: int, exec_ts: int, exec_price: float):
    with get_conn() as conn:
        conn.execute(
            "UPDATE orders SET status='FILLED', executed_ts=?, executed_price=? WHERE id=?",
            (exec_ts, float(exec_price), order_id),
        )


def cancel_order(guild_id: int, user_id: int, order_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT status FROM orders WHERE id=? AND guild_id=? AND user_id=?",
            (order_id, guild_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return False
        if str(row[0]) != 'OPEN':
            return False
        conn.execute("UPDATE orders SET status='CANCELLED', note='cancelled by user' WHERE id=?", (order_id,))
        return True


# ----------------------
# Auction APIs
# ----------------------
import time as _time


def create_auction(seller_id: int, name: str, emoji: str, qty: int, start_price: int, duration_seconds: int, guild_id: int | None = None) -> int:
    """Create an auction by moving items from seller inventory into escrow.

    Returns auction id. Raises ValueError on invalid input or insufficient qty.
    """
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    if start_price < 0:
        raise ValueError("Start price must be >= 0")
    if duration_seconds < 3600 or duration_seconds > 30 * 24 * 3600:
        raise ValueError("Duration must be between 1 hour and 30 days")
    now = int(_time.time())
    end_at = now + duration_seconds
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Ensure item exists; then decrease from seller inventory
        # Reuse discard_item logic inline to keep in same transaction
        cur = conn.execute(
            "SELECT id FROM items WHERE name=? AND emoji=?",
            (name.strip(), emoji.strip()),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Item not found in catalog")
        item_id = int(row[0])
        cur = conn.execute(
            "SELECT qty FROM inventory WHERE user_id=? AND item_id=?",
            (seller_id, item_id),
        )
        row = cur.fetchone()
        have = int(row[0]) if row else 0
        if have < qty:
            raise ValueError("Insufficient item quantity")
        new_qty = have - qty
        if new_qty == 0:
            conn.execute("DELETE FROM inventory WHERE user_id=? AND item_id=?", (seller_id, item_id))
        else:
            conn.execute(
                "UPDATE inventory SET qty=? WHERE user_id=? AND item_id=?",
                (new_qty, seller_id, item_id),
            )

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
    with get_conn() as conn:
        if query:
            q = f"%{query.lower()}%"
            if guild_id is not None:
                cur = conn.execute(
                    """
                    SELECT id, seller_id, name, emoji, qty, start_price, current_bid, current_bidder_id, end_at
                    FROM auctions
                    WHERE status='open' AND end_at > ? AND guild_id = ? AND (LOWER(name) LIKE ? OR emoji LIKE ?)
                    ORDER BY end_at ASC
                    LIMIT ? OFFSET ?
                    """,
                    (int(_time.time()), guild_id, q, q, limit, offset),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT id, seller_id, name, emoji, qty, start_price, current_bid, current_bidder_id, end_at
                    FROM auctions
                    WHERE status='open' AND end_at > ? AND (LOWER(name) LIKE ? OR emoji LIKE ?)
                    ORDER BY end_at ASC
                    LIMIT ? OFFSET ?
                    """,
                    (int(_time.time()), q, q, limit, offset),
                )
        else:
            if guild_id is not None:
                cur = conn.execute(
                    """
                    SELECT id, seller_id, name, emoji, qty, start_price, current_bid, current_bidder_id, end_at
                    FROM auctions
                    WHERE status='open' AND end_at > ? AND guild_id = ?
                    ORDER BY end_at ASC
                    LIMIT ? OFFSET ?
                    """,
                    (int(_time.time()), guild_id, limit, offset),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT id, seller_id, name, emoji, qty, start_price, current_bid, current_bidder_id, end_at
                    FROM auctions
                    WHERE status='open' AND end_at > ?
                    ORDER BY end_at ASC
                    LIMIT ? OFFSET ?
                    """,
                    (int(_time.time()), limit, offset),
                )
        return cur.fetchall()


def count_open_auctions(query: str | None = None, guild_id: int | None = None) -> int:
    with get_conn() as conn:
        if query:
            q = f"%{query.lower()}%"
            if guild_id is not None:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM auctions WHERE status='open' AND end_at > ? AND guild_id = ? AND (LOWER(name) LIKE ? OR emoji LIKE ?)",
                    (int(_time.time()), guild_id, q, q),
                )
            else:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM auctions WHERE status='open' AND end_at > ? AND (LOWER(name) LIKE ? OR emoji LIKE ?)",
                    (int(_time.time()), q, q),
                )
        else:
            if guild_id is not None:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM auctions WHERE status='open' AND end_at > ? AND guild_id = ?",
                    (int(_time.time()), guild_id),
                )
            else:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM auctions WHERE status='open' AND end_at > ?",
                    (int(_time.time()),),
                )
        return int(cur.fetchone()[0])

def get_auction_guild(aid: int) -> tuple[int | None, int, str]:
    """Return (guild_id, end_at, status) for an auction."""
    with get_conn() as conn:
        cur = conn.execute("SELECT guild_id, end_at, status FROM auctions WHERE id=?", (aid,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Auction not found")
        gid, end_at, status = row
        return (int(gid) if gid is not None else None, int(end_at), str(status))

def list_due_unsold_auctions(limit: int = 50):
    """Return auctions that are due, open, and have no bids."""
    now = int(_time.time())
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
        conn.execute(
            "UPDATE auctions SET status='closed', winner_id=NULL, winning_bid=NULL WHERE id=?",
            (aid,),
        )


def place_bid(auction_id: int, bidder_id: int, amount: int):
    """Place a bid with escrow: deduct from bidder, refund previous top bidder.

    Returns (current_bid, current_bidder_id).
    """
    if amount <= 0:
        raise ValueError("Bid must be positive")
    now = int(_time.time())
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

        # Ensure bidder has funds and deduct
        # Ensure bidder balance row exists
        cur = conn.execute("SELECT balance FROM balances WHERE user_id=?", (bidder_id,))
        rowb = cur.fetchone()
        if rowb is None:
            conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (bidder_id, DEFAULT_BALANCE))
            bal = DEFAULT_BALANCE
        else:
            bal = int(rowb[0])
        if bal < amount:
            raise ValueError("Insufficient funds")
        conn.execute("UPDATE balances SET balance=? WHERE user_id=?", (bal - amount, bidder_id))

        # Refund previous top bidder, if any
        if current_bidder_id is not None and current_bid is not None:
            cur = conn.execute("SELECT balance FROM balances WHERE user_id=?", (current_bidder_id,))
            rowp = cur.fetchone()
            prev_bal = int(rowp[0]) if rowp else DEFAULT_BALANCE
            if rowp is None:
                conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (current_bidder_id, prev_bal))
            conn.execute(
                "UPDATE balances SET balance=? WHERE user_id=?",
                (prev_bal + int(current_bid), current_bidder_id),
            )

        # Update auction price and top bidder
        conn.execute(
            "UPDATE auctions SET current_bid=?, current_bidder_id=? WHERE id=?",
            (amount, bidder_id, auction_id),
        )
        conn.execute(
            "INSERT INTO auction_bids(auction_id, bidder_id, amount, created_at) VALUES(?, ?, ?, ?)",
            (auction_id, bidder_id, amount, now),
        )
        return amount, bidder_id


def finalize_due_auctions(max_to_close: int = 50) -> int:
    """Finalize auctions whose end_at has passed.

    Returns number of auctions closed.
    """
    now = int(_time.time())
    closed = 0
    with get_conn() as conn:
        # fetch a batch to avoid long locks
        cur = conn.execute(
            "SELECT id, seller_id, name, emoji, qty, current_bid, current_bidder_id FROM auctions WHERE status='open' AND end_at <= ? LIMIT ?",
            (now, max_to_close),
        )
        rows = cur.fetchall()
        for (aid, seller_id, name, emoji, qty, current_bid, current_bidder_id) in rows:
            conn.execute("BEGIN IMMEDIATE")
            # Recheck status for this row
            cur2 = conn.execute(
                "SELECT status, current_bid, current_bidder_id FROM auctions WHERE id=?",
                (aid,),
            )
            st, cb, cbid = cur2.fetchone()
            if st != 'open':
                continue
            # If no bids, return item to seller
            if cbid is None or cb is None:
                # return items to seller
                # upsert inventory
                conn.execute(
                    """
                    INSERT INTO inventory(user_id, item_id, qty)
                    SELECT ?, i.id, ? FROM items i WHERE i.name=? AND i.emoji=?
                    ON CONFLICT(user_id, item_id) DO UPDATE SET qty=qty+excluded.qty
                    """,
                    (seller_id, qty, name, emoji),
                )
                conn.execute(
                    "UPDATE auctions SET status='closed', winner_id=NULL, winning_bid=NULL WHERE id=?",
                    (aid,),
                )
                closed += 1
                continue
            # There is a winner: give item to winner, pay seller
            # Give item
            conn.execute(
                """
                INSERT INTO inventory(user_id, item_id, qty)
                SELECT ?, i.id, ? FROM items i WHERE i.name=? AND i.emoji=?
                ON CONFLICT(user_id, item_id) DO UPDATE SET qty=qty+excluded.qty
                """,
                (cbid, qty, name, emoji),
            )
            # Pay seller (funds already deducted from winner; now credit seller)
            cur3 = conn.execute("SELECT balance FROM balances WHERE user_id=?", (seller_id,))
            row = cur3.fetchone()
            seller_bal = int(row[0]) if row else DEFAULT_BALANCE
            if row is None:
                conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (seller_id, seller_bal))
            conn.execute(
                "UPDATE balances SET balance=? WHERE user_id=?",
                (seller_bal + int(cb), seller_id),
            )
            conn.execute(
                "UPDATE auctions SET status='closed', winner_id=?, winning_bid=? WHERE id=?",
                (cbid, cb, aid),
            )
            closed += 1
    return closed


def finalize_due_auctions_details(max_to_close: int = 50):
    """Finalize due auctions and return detailed results for notifications.

    Returns a list of dicts with keys:
      - id, guild_id, seller_id, name, emoji, qty,
      - status: 'sold' | 'unsold_return',
      - winner_id (optional), winning_bid (optional)
    """
    now = int(_time.time())
    results: list[dict] = []
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, guild_id, seller_id, name, emoji, qty, current_bid, current_bidder_id FROM auctions WHERE status='open' AND end_at <= ? LIMIT ?",
            (now, max_to_close),
        )
        rows = cur.fetchall()
        for (aid, gid, seller_id, name, emoji, qty, current_bid, current_bidder_id) in rows:
            conn.execute("BEGIN IMMEDIATE")
            cur2 = conn.execute(
                "SELECT status, current_bid, current_bidder_id FROM auctions WHERE id=?",
                (aid,),
            )
            st, cb, cbid = cur2.fetchone()
            if st != 'open':
                continue
            if cbid is None or cb is None:
                # unsold: return to seller
                conn.execute(
                    """
                    INSERT INTO inventory(user_id, item_id, qty)
                    SELECT ?, i.id, ? FROM items i WHERE i.name=? AND i.emoji=?
                    ON CONFLICT(user_id, item_id) DO UPDATE SET qty=qty+excluded.qty
                    """,
                    (seller_id, qty, name, emoji),
                )
                conn.execute(
                    "UPDATE auctions SET status='closed', winner_id=NULL, winning_bid=NULL WHERE id=?",
                    (aid,),
                )
                results.append({
                    'id': int(aid),
                    'guild_id': int(gid) if gid is not None else None,
                    'seller_id': int(seller_id),
                    'name': str(name),
                    'emoji': str(emoji),
                    'qty': int(qty),
                    'status': 'unsold_return',
                })
                continue
            # sold case
            conn.execute(
                """
                INSERT INTO inventory(user_id, item_id, qty)
                SELECT ?, i.id, ? FROM items i WHERE i.name=? AND i.emoji=?
                ON CONFLICT(user_id, item_id) DO UPDATE SET qty=qty+excluded.qty
                """,
                (cbid, qty, name, emoji),
            )
            # pay seller
            cur3 = conn.execute("SELECT balance FROM balances WHERE user_id=?", (seller_id,))
            row = cur3.fetchone()
            seller_bal = int(row[0]) if row else DEFAULT_BALANCE
            if row is None:
                conn.execute("INSERT INTO balances(user_id, balance) VALUES(?, ?)", (seller_id, seller_bal))
            conn.execute(
                "UPDATE balances SET balance=? WHERE user_id=?",
                (seller_bal + int(cb), seller_id),
            )
            conn.execute(
                "UPDATE auctions SET status='closed', winner_id=?, winning_bid=? WHERE id=?",
                (cbid, cb, aid),
            )
            results.append({
                'id': int(aid),
                'guild_id': int(gid) if gid is not None else None,
                'seller_id': int(seller_id),
                'name': str(name),
                'emoji': str(emoji),
                'qty': int(qty),
                'status': 'sold',
                'winner_id': int(cbid),
                'winning_bid': int(cb),
            })
    return results


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


def discard_item(user_id: int, name: str, emoji: str, qty: int = 1) -> int:
    """Discard `qty` of an item from user's inventory. Returns remaining qty.

    Raises ValueError if item not found or insufficient quantity.
    """
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "SELECT id FROM items WHERE name=? AND emoji=?",
            (name.strip(), emoji.strip()),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Item not found")
        item_id = int(row[0])

        cur = conn.execute(
            "SELECT qty FROM inventory WHERE user_id=? AND item_id=?",
            (user_id, item_id),
        )
        row = cur.fetchone()
        current = int(row[0]) if row else 0
        if current < qty:
            raise ValueError("Insufficient item quantity")

        new_qty = current - qty
        if new_qty == 0:
            conn.execute(
                "DELETE FROM inventory WHERE user_id=? AND item_id=?",
                (user_id, item_id),
            )
        else:
            conn.execute(
                "UPDATE inventory SET qty=? WHERE user_id=? AND item_id=?",
                (new_qty, user_id, item_id),
            )
        return new_qty
