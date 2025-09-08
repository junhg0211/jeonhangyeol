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


def get_team_parent(guild_id: int, team_id: int) -> int | None:
    with get_conn() as conn:
        row = conn.execute("SELECT parent_id FROM teams WHERE guild_id=? AND id=?", (guild_id, team_id)).fetchone()
        return int(row[0]) if row and row[0] is not None else None


def list_team_children(guild_id: int, parent_id: int) -> list[int]:
    with get_conn() as conn:
        cur = conn.execute("SELECT id FROM teams WHERE guild_id=? AND parent_id=? ORDER BY id ASC", (guild_id, parent_id))
        return [int(i) for (i,) in cur.fetchall()]


def team_subtree_has_members(guild_id: int, team_id: int) -> bool:
    """Return True if any user is assigned to the given team or its descendants."""
    with get_conn() as conn:
        # gather subtree ids via DFS
        to_visit = [int(team_id)]
        ids: list[int] = []
        while to_visit:
            cur_id = to_visit.pop()
            ids.append(cur_id)
            rows = conn.execute("SELECT id FROM teams WHERE guild_id=? AND parent_id=?", (guild_id, cur_id)).fetchall()
            to_visit.extend(int(r[0]) for r in rows)
        # check any membership
        qmarks = ",".join(["?"] * len(ids))
        row = conn.execute(
            f"SELECT 1 FROM user_teams WHERE guild_id=? AND team_id IN ({qmarks}) LIMIT 1",
            (guild_id, *ids),
        ).fetchone()
        return row is not None


def delete_team_subtree(guild_id: int, team_id: int) -> int:
    """Delete the team and all its descendant teams. Returns deleted row count."""
    with get_conn() as conn:
        # collect subtree
        to_visit = [int(team_id)]
        ids: list[int] = []
        while to_visit:
            cur_id = to_visit.pop()
            ids.append(cur_id)
            rows = conn.execute("SELECT id FROM teams WHERE guild_id=? AND parent_id=?", (guild_id, cur_id)).fetchall()
            to_visit.extend(int(r[0]) for r in rows)
        # delete all (children first not required without FK)
        qmarks = ",".join(["?"] * len(ids))
        cur = conn.execute(f"DELETE FROM teams WHERE guild_id=? AND id IN ({qmarks})", (guild_id, *ids))
        return cur.rowcount or 0


def prune_empty_upwards(guild_id: int, team_id: int | None) -> int:
    """From a starting team, delete the subtree if it has no members; then climb to parent and repeat.

    Never deletes the synthetic root team. Returns total deleted team rows.
    """
    if team_id is None:
        return 0
    deleted = 0
    with get_conn() as conn:
        # fetch root id to protect
        row = conn.execute("SELECT id FROM teams WHERE guild_id=? AND name=? AND parent_id IS NULL", (guild_id, TEAM_ROOT_NAME)).fetchone()
        root_id = int(row[0]) if row else None
    cur_id = int(team_id)
    while True:
        if root_id is not None and cur_id == root_id:
            break
        # if team no longer exists, stop
        with get_conn() as conn:
            row = conn.execute("SELECT id, parent_id FROM teams WHERE guild_id=? AND id=?", (guild_id, cur_id)).fetchone()
        if not row:
            break
        parent_id = int(row[1]) if row[1] is not None else None
        if not team_subtree_has_members(guild_id, cur_id):
            deleted += delete_team_subtree(guild_id, cur_id)
            if parent_id is None:
                break
            cur_id = parent_id
            continue
        else:
            break
    return deleted

def get_user_team_id(guild_id: int, user_id: int) -> int | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT team_id FROM user_teams WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
        return int(row[0]) if row else None


def get_team_path_names(guild_id: int, team_id: int) -> list[str]:
    """Return team path from root(exclusive) to leaf as names.

    If the team_id is invalid or points to the synthetic root, returns [].
    """
    with get_conn() as conn:
        # build upward then reverse
        names: list[str] = []
        cur_id = int(team_id)
        while True:
            row = conn.execute(
                "SELECT name, parent_id FROM teams WHERE guild_id=? AND id=?",
                (guild_id, cur_id),
            ).fetchone()
            if not row:
                break
            name, parent_id = str(row[0]), (int(row[1]) if row[1] is not None else None)
            if name != TEAM_ROOT_NAME:
                names.append(name)
            if parent_id is None:
                break
            cur_id = parent_id
        names.reverse()
        return names


def set_rank_roles(guild_id: int, role_names: list[str]) -> None:
    """Store the list of rank role names in guild_settings.rank_role_names (CSV)."""
    role_names = [r.strip() for r in role_names if r and r.strip()]
    csv = ",".join(role_names)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO guild_settings(guild_id, rank_role_names) VALUES(?, ?)\n             ON CONFLICT(guild_id) DO UPDATE SET rank_role_names=excluded.rank_role_names",
            (guild_id, csv),
        )


def get_rank_roles(guild_id: int) -> list[str]:
    """Get configured rank role names, or default list if not set."""
    default = ["회장", "사장", "부장", "차장", "과장", "대리", "사수", "부사수", "신입"]
    with get_conn() as conn:
        row = conn.execute(
            "SELECT rank_role_names FROM guild_settings WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
        if not row or row[0] is None or str(row[0]).strip() == "":
            return default
        names = [n.strip() for n in str(row[0]).split(",")]
        return [n for n in names if n]


__all__ = [
    'TEAM_ROOT_NAME',
    'ensure_team_path','set_user_team','list_teams','list_team_members',
    'get_user_team_id','get_team_path_names','set_rank_roles','get_rank_roles',
]
