from .core import get_conn


def set_main_chat_channel(guild_id: int, channel_id: int | None) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO guild_settings(guild_id, main_chat_channel_id) VALUES(?, ?) ON CONFLICT(guild_id) DO UPDATE SET main_chat_channel_id=excluded.main_chat_channel_id", (guild_id, channel_id))


def get_main_chat_channel(guild_id: int) -> int | None:
    with get_conn() as conn:
        row = conn.execute("SELECT main_chat_channel_id FROM guild_settings WHERE guild_id=?", (guild_id,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None


def set_announce_channel(guild_id: int, channel_id: int | None) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO guild_settings(guild_id, announce_channel_id) VALUES(?, ?) ON CONFLICT(guild_id) DO UPDATE SET announce_channel_id=excluded.announce_channel_id", (guild_id, channel_id))


def get_announce_channel(guild_id: int) -> int | None:
    with get_conn() as conn:
        row = conn.execute("SELECT announce_channel_id FROM guild_settings WHERE guild_id=?", (guild_id,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None


# ---- Notify channel (alias to announce channel for now) ----
def set_notify_channel(guild_id: int, channel_id: int | None) -> None:
    """Alias to set_announce_channel for a generic notify destination."""
    return set_announce_channel(guild_id, channel_id)


def get_notify_channel(guild_id: int) -> int | None:
    """Prefer announce channel; fall back to main chat if not set."""
    ch = get_announce_channel(guild_id)
    if ch is not None:
        return ch
    return get_main_chat_channel(guild_id)


# ---- Activity index alerts toggle ----
def set_index_alerts_enabled(guild_id: int, enabled: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO guild_settings(guild_id, index_alerts_enabled) VALUES(?, ?)\n             ON CONFLICT(guild_id) DO UPDATE SET index_alerts_enabled=excluded.index_alerts_enabled",
            (guild_id, 1 if enabled else 0),
        )


def get_index_alerts_enabled(guild_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT index_alerts_enabled FROM guild_settings WHERE guild_id=?", (guild_id,)).fetchone()
        return bool(int(row[0])) if row and row[0] is not None else False


def add_announcement(guild_id: int, content: str) -> int:
    import time
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO announcements(guild_id, content, created_ts) VALUES(?, ?, ?)", (guild_id, content.strip(), int(time.time())))
        return int(cur.lastrowid)


def list_announcements(guild_id: int):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, content, active FROM announcements WHERE guild_id=? ORDER BY id ASC", (guild_id,))
        return [(int(i), str(c), int(a)) for (i, c, a) in cur.fetchall()]


def remove_announcement(guild_id: int, ann_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM announcements WHERE id=? AND guild_id=?", (ann_id, guild_id))
        return cur.rowcount > 0


def clear_announcements(guild_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM announcements WHERE guild_id=?", (guild_id,))


def has_announcements(guild_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("SELECT 1 FROM announcements WHERE guild_id=? AND active=1 LIMIT 1", (guild_id,))
        return cur.fetchone() is not None


def next_announcement(guild_id: int, index: int) -> str | None:
    with get_conn() as conn:
        rows = [str(r[0]) for r in conn.execute("SELECT content FROM announcements WHERE guild_id=? AND active=1 ORDER BY id ASC", (guild_id,)).fetchall()]
        if not rows:
            return None
        return rows[index % len(rows)]


def incr_message_count(guild_id: int, channel_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT count FROM message_counters WHERE guild_id=? AND channel_id=?", (guild_id, channel_id)).fetchone()
        if row is None:
            conn.execute("INSERT INTO message_counters(guild_id, channel_id, count) VALUES(?, ?, 1)", (guild_id, channel_id))
            return 1
        count = int(row[0]) + 1
        conn.execute("UPDATE message_counters SET count=? WHERE guild_id=? AND channel_id=?", (count, guild_id, channel_id))
        return count

__all__ = [
    'set_main_chat_channel','get_main_chat_channel','set_announce_channel','get_announce_channel',
    'set_notify_channel','get_notify_channel','set_index_alerts_enabled','get_index_alerts_enabled',
    'add_announcement','list_announcements','remove_announcement','clear_announcements',
    'has_announcements','next_announcement','incr_message_count',
]
