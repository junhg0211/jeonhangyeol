from .core import get_conn
import time as _time
import re as _re


def join_patent_game(guild_id: int, user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO patent_participants(guild_id, user_id) VALUES(?, ?)", (guild_id, user_id))


def leave_patent_game(guild_id: int, user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM patent_participants WHERE guild_id=? AND user_id=?", (guild_id, user_id))


def is_patent_participant(guild_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("SELECT 1 FROM patent_participants WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        return cur.fetchone() is not None


def patent_min_price(word: str) -> int:
    w = (word or "").strip()
    n = len(w)
    if n <= 2:
        return 5000
    if n == 3:
        return 3000
    if n == 4:
        return 1500
    if n == 5:
        return 800
    return 400


def patent_usage_fee(price: int) -> int:
    try:
        return max(1, int(price) // 50)
    except Exception:
        return 1


def add_patent(guild_id: int, owner_id: int, word: str, price: int) -> int:
    w = (word or "").strip()
    if not w:
        raise ValueError("단어가 비어 있습니다.")
    if price < patent_min_price(w):
        raise ValueError("가격이 최소 요구 금액보다 낮습니다.")
    now = int(_time.time())
    key = w.casefold()
    with get_conn() as conn:
        try:
            cur = conn.execute("INSERT INTO patents(guild_id, owner_id, word, price, created_ts) VALUES(?, ?, ?, ?, ?)", (guild_id, owner_id, key, int(price), now))
            pid = int(cur.lastrowid)
        except Exception as e:
            # likely unique constraint
            raise ValueError("이미 출원된 단어입니다.")
    # Inventory representation handled in higher layer; DB owns patent record
    return pid


def cancel_patent(guild_id: int, owner_id: int, word: str) -> bool:
    key = (word or "").strip().casefold()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM patents WHERE guild_id=? AND owner_id=? AND word=?", (guild_id, owner_id, key))
        return cur.rowcount > 0


def transfer_patent(guild_id: int, from_id: int, to_id: int, word: str) -> bool:
    key = (word or "").strip().casefold()
    with get_conn() as conn:
        cur = conn.execute("UPDATE patents SET owner_id=? WHERE guild_id=? AND owner_id=? AND word=?", (to_id, guild_id, from_id, key))
        return cur.rowcount > 0


def list_patents(guild_id: int):
    with get_conn() as conn:
        cur = conn.execute("SELECT owner_id, word, price FROM patents WHERE guild_id=? ORDER BY LENGTH(word) ASC, price DESC", (guild_id,))
        return [(int(oid), str(w), int(p)) for (oid, w, p) in cur.fetchall()]


def find_patent_hits(guild_id: int, content: str):
    text = (content or "").casefold()
    if not text:
        return []
    with get_conn() as conn:
        cur = conn.execute("SELECT owner_id, word, price FROM patents WHERE guild_id=?", (guild_id,))
        hits, seen = [], set()
        for oid, w, p in cur.fetchall():
            if w in seen:
                continue
            if w and w in text:
                seen.add(w)
                hits.append((w, int(oid), int(p)))
        return hits


def censor_words(content: str, words: list[str]) -> str:
    s = content
    for w in sorted(set(words), key=len, reverse=True):
        try:
            pat = _re.compile(_re.escape(w), _re.IGNORECASE)
            s = pat.sub(lambda m: f"||{m.group(0)}||", s)
        except Exception:
            pass
    return s


def log_patent_detection(guild_id: int, user_id: int, channel_id: int | None, message_id: int | None, words: list[str], total_fee: int, censored: bool) -> None:
    now = int(_time.time())
    words_str = ",".join(sorted(set([w for w in words if w])))
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO patent_logs(ts, guild_id, user_id, channel_id, message_id, words, total_fee, censored) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (now, guild_id, user_id, channel_id if channel_id is not None else None, message_id if message_id is not None else None, words_str, int(total_fee), 1 if censored else 0),
        )


def get_recent_patent_logs(guild_id: int, limit: int = 20):
    with get_conn() as conn:
        cur = conn.execute("SELECT ts, user_id, channel_id, message_id, words, total_fee, censored FROM patent_logs WHERE guild_id=? ORDER BY id DESC LIMIT ?", (guild_id, int(limit)))
        return [(int(ts), int(uid), (int(ch) if ch is not None else None), (int(mid) if mid is not None else None), str(words), int(fee), bool(c)) for (ts, uid, ch, mid, words, fee, c) in cur.fetchall()]


def get_user_patent_logs(guild_id: int, user_id: int, limit: int = 20):
    with get_conn() as conn:
        cur = conn.execute("SELECT ts, user_id, channel_id, message_id, words, total_fee, censored FROM patent_logs WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT ?", (guild_id, user_id, int(limit)))
        return [(int(ts), int(uid), (int(ch) if ch is not None else None), (int(mid) if mid is not None else None), str(words), int(fee), bool(c)) for (ts, uid, ch, mid, words, fee, c) in cur.fetchall()]


def list_expired_unauctioned_patents(limit: int = 50):
    now = int(_time.time())
    cutoff = now - 14 * 24 * 3600
    with get_conn() as conn:
        cur = conn.execute("SELECT id, guild_id, owner_id, word, price, created_ts FROM patents WHERE created_ts <= ? AND (auctioned IS NULL OR auctioned = 0) ORDER BY created_ts ASC LIMIT ?", (cutoff, int(limit)))
        return [(int(i), int(g), int(o), str(w), int(p), int(cts)) for (i, g, o, w, p, cts) in cur.fetchall()]


def mark_patent_auctioned(patent_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE patents SET auctioned=1 WHERE id=?", (int(patent_id),))


def get_patent_price(guild_id: int, word: str) -> int | None:
    key = (word or "").strip().casefold()
    with get_conn() as conn:
        cur = conn.execute("SELECT price FROM patents WHERE guild_id=? AND word=?", (guild_id, key))
        row = cur.fetchone()
        return int(row[0]) if row else None

__all__ = [name for name in (
    'join_patent_game','leave_patent_game','is_patent_participant','patent_min_price','patent_usage_fee',
    'add_patent','cancel_patent','transfer_patent','list_patents','find_patent_hits','censor_words',
    'log_patent_detection','get_recent_patent_logs','get_user_patent_logs','list_expired_unauctioned_patents',
    'mark_patent_auctioned','get_patent_price',
)]
