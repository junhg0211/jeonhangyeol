from .core import get_conn

TEAM_ROOT_NAME = "__ROOT__"


def _ensure_team_root(guild_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM teams WHERE guild_id=? AND name=? AND parent_id IS NULL", (guild_id, TEAM_ROOT_NAME)).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute("INSERT INTO teams(guild_id, name, parent_id) VALUES(?, ?, NULL)", (guild_id, TEAM_ROOT_NAME))
        return int(cur.lastrowid)


def _get_or_create_child(guild_id: int, parent_id: int, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("팀 이름이 비어 있습니다.")
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM teams WHERE guild_id=? AND name=? AND parent_id=?", (guild_id, name, parent_id)).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute("INSERT INTO teams(guild_id, name, parent_id) VALUES(?, ?, ?)", (guild_id, name, parent_id))
        return int(cur.lastrowid)


def ensure_team_path(guild_id: int, path: str) -> int:
    root_id = _ensure_team_root(guild_id)
    tokens = [t for t in (path or "").split() if t]
    if not tokens:
        raise ValueError("팀 경로가 비어 있습니다.")
    parent = root_id
    for tok in tokens:
        parent = _get_or_create_child(guild_id, parent, tok)
    return parent


def set_user_team(guild_id: int, user_id: int, team_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_teams(guild_id, user_id, team_id) VALUES(?, ?, ?)\n             ON CONFLICT(guild_id, user_id) DO UPDATE SET team_id=excluded.team_id",
            (guild_id, user_id, team_id),
        )


def list_teams(guild_id: int):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, name, parent_id FROM teams WHERE guild_id=? ORDER BY (parent_id IS NOT NULL), COALESCE(parent_id, 0), id ASC",
            (guild_id,),
        )
        return [(int(i), str(n), (int(p) if p is not None else None)) for (i, n, p) in cur.fetchall()]


def list_team_members(guild_id: int, team_id: int):
    with get_conn() as conn:
        cur = conn.execute("SELECT user_id FROM user_teams WHERE guild_id=? AND team_id=? ORDER BY user_id ASC", (guild_id, team_id))
        return [int(u) for (u,) in cur.fetchall()]

__all__ = ['TEAM_ROOT_NAME','ensure_team_path','set_user_team','list_teams','list_team_members']
