import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), "data.sqlite3"))
KST = ZoneInfo("Asia/Seoul")


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
    """Create tables if not exist and run lightweight migrations."""
    now = int(datetime.now(KST).timestamp())
    with get_conn() as conn:
        # Economy
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS balances (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL
            );
            """
        )

        # Attendance
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_date TEXT,
                streak INTEGER NOT NULL DEFAULT 0,
                max_streak INTEGER NOT NULL DEFAULT 0,
                total_days INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                reward INTEGER NOT NULL
            );
            """
        )

        # Patents
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS patent_participants (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS patents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                price INTEGER NOT NULL,
                created_ts INTEGER NOT NULL,
                auctioned INTEGER,
                UNIQUE (guild_id, word)
            );
            """
        )
        try:
            conn.execute("ALTER TABLE patents ADD COLUMN auctioned INTEGER")
        except Exception:
            pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS patent_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER,
                message_id INTEGER,
                words TEXT NOT NULL,
                total_fee INTEGER NOT NULL,
                censored INTEGER NOT NULL
            );
            """
        )

        # Trading
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_ticks (
                guild_id INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                delta REAL NOT NULL,
                PRIMARY KEY (guild_id, ts, symbol)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instruments (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                category TEXT
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
                side TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price REAL NOT NULL,
                notional INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_ts INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty INTEGER NOT NULL,
                order_type TEXT NOT NULL,
                limit_price REAL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                executed_ts INTEGER,
                executed_price REAL,
                note TEXT
            );
            """
        )

        # Activity indices
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
        try:
            conn.execute("ALTER TABLE activity_indices ADD COLUMN high_idx REAL")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE activity_indices ADD COLUMN low_idx REAL")
        except Exception:
            pass
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

        # Settings & announcements
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                auction_channel_id INTEGER,
                index_alerts_enabled INTEGER,
                main_chat_channel_id INTEGER,
                announce_channel_id INTEGER,
                rank_role_names TEXT
            );
            """
        )
        for col in ("index_alerts_enabled", "main_chat_channel_id", "announce_channel_id"):
            try:
                conn.execute(f"ALTER TABLE guild_settings ADD COLUMN {col} INTEGER")
            except Exception:
                pass
        try:
            conn.execute("ALTER TABLE guild_settings ADD COLUMN rank_role_names TEXT")
        except Exception:
            pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_ts INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message_counters (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, channel_id)
            );
            """
        )

        # Items & inventory
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

        # Auctions
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
                winning_bid INTEGER,
                guild_id INTEGER
            );
            """
        )
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
                created_at INTEGER NOT NULL
            );
            """
        )

        # Auto transfer
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                from_user INTEGER NOT NULL,
                to_user INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                period_days INTEGER NOT NULL,
                start_date TEXT NOT NULL,
                last_date TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_transfer_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                auto_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                message TEXT
            );
            """
        )

        # Teams (DB-backed)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                parent_id INTEGER,
                UNIQUE(guild_id, name, parent_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_teams (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                team_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            """
        )

__all__ = ['get_conn', 'init_db', 'KST', 'DB_PATH']
