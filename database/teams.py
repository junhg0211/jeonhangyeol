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


def clear_user_team(guild_id: int, user_id: int) -> None:
    """Remove user's team assignment (row delete)."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM user_teams WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
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


def count_team_members(guild_id: int, team_id: int) -> int:
    """Count direct members assigned to a given team."""
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM user_teams WHERE guild_id=? AND team_id=?", (guild_id, team_id)).fetchone()
        return int(row[0]) if row else 0


def count_team_subtree_members(guild_id: int, team_id: int) -> int:
    """Count members in the team including all descendant teams."""
    with get_conn() as conn:
        to_visit = [int(team_id)]
        ids: list[int] = []
        while to_visit:
            cur_id = to_visit.pop()
            ids.append(cur_id)
            rows = conn.execute("SELECT id FROM teams WHERE guild_id=? AND parent_id=?", (guild_id, cur_id)).fetchall()
            to_visit.extend(int(r[0]) for r in rows)
        qmarks = ",".join(["?"] * len(ids))
        row = conn.execute(
            f"SELECT COUNT(*) FROM user_teams WHERE guild_id=? AND team_id IN ({qmarks})",
            (guild_id, *ids),
        ).fetchone()
        return int(row[0]) if row else 0


def get_team_parent(guild_id: int, team_id: int) -> int | None:
    with get_conn() as conn:
        row = conn.execute("SELECT parent_id FROM teams WHERE guild_id=? AND id=?", (guild_id, team_id)).fetchone()
        return int(row[0]) if row and row[0] is not None else None


def list_team_children(guild_id: int, parent_id: int) -> list[int]:
    with get_conn() as conn:
        cur = conn.execute("SELECT id FROM teams WHERE guild_id=? AND parent_id=? ORDER BY id ASC", (guild_id, parent_id))
        return [int(i) for (i,) in cur.fetchall()]


def _find_child_id(conn, guild_id: int, parent_id: int | None, name: str) -> int | None:
    if parent_id is None:
        row = conn.execute(
            "SELECT id FROM teams WHERE guild_id=? AND name=? AND parent_id IS NULL",
            (guild_id, name),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM teams WHERE guild_id=? AND name=? AND parent_id=?",
            (guild_id, name, parent_id),
        ).fetchone()
    return int(row[0]) if row else None


def find_team_by_path(guild_id: int, path: str) -> int | None:
    tokens = [t for t in (path or "").split() if t]
    if not tokens:
        return None
    with get_conn() as conn:
        # get root id
        root = conn.execute(
            "SELECT id FROM teams WHERE guild_id=? AND name=? AND parent_id IS NULL",
            (guild_id, TEAM_ROOT_NAME),
        ).fetchone()
        if not root:
            return None
        parent = int(root[0])
        for tok in tokens:
            cid = _find_child_id(conn, guild_id, parent, tok)
            if cid is None:
                return None
            parent = cid
        return parent


def get_descendant_team_ids(guild_id: int, team_id: int) -> list[int]:
    ids: list[int] = []
    with get_conn() as conn:
        to_visit = [int(team_id)]
        while to_visit:
            cur = to_visit.pop()
            ids.append(cur)
            rows = conn.execute("SELECT id FROM teams WHERE guild_id=? AND parent_id=?", (guild_id, cur)).fetchall()
            to_visit.extend(int(r[0]) for r in rows)
    return ids


def clear_membership_subtree(guild_id: int, team_id: int) -> int:
    ids = get_descendant_team_ids(guild_id, team_id)
    if not ids:
        return 0
    with get_conn() as conn:
        q = ",".join(["?"] * len(ids))
        cur = conn.execute(
            f"DELETE FROM user_teams WHERE guild_id=? AND team_id IN ({q})",
            (guild_id, *ids),
        )
        return cur.rowcount or 0


def delete_empty_ancestors(guild_id: int, team_id: int) -> int:
    """Delete empty ancestor teams up to (but not including) the synthetic root.

    A team is considered deletable if it has no members in its subtree AND has no children rows.
    Returns number of deleted team nodes.
    """
    deleted = 0
    parent = get_team_parent(guild_id, team_id)
    while parent is not None:
        # stop at root
        with get_conn() as conn:
            row = conn.execute("SELECT name, parent_id FROM teams WHERE guild_id=? AND id=?", (guild_id, parent)).fetchone()
        if not row:
            break
        name = str(row[0])
        if name == TEAM_ROOT_NAME:
            break
        # check children and members
        children = list_team_children(guild_id, parent)
        if children:
            break
        if team_subtree_has_members(guild_id, parent):
            break
        # delete this parent and move up
        with get_conn() as conn:
            conn.execute("DELETE FROM teams WHERE guild_id=? AND id=?", (guild_id, parent))
        deleted += 1
        parent = get_team_parent(guild_id, parent)
    return deleted


def delete_team_path_atomic(guild_id: int, path: str) -> tuple[int, int]:
    """Atomically clear memberships under a team path and remove empty team nodes.

    Returns (cleared_memberships, removed_team_nodes).
    """
    tokens = [t for t in (path or "").split() if t]
    if not tokens:
        return (0, 0)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # find root
        row = conn.execute(
            "SELECT id FROM teams WHERE guild_id=? AND name=? AND parent_id IS NULL",
            (guild_id, TEAM_ROOT_NAME),
        ).fetchone()
        if not row:
            return (0, 0)
        parent = int(row[0])
        for tok in tokens:
            child = conn.execute(
                "SELECT id FROM teams WHERE guild_id=? AND name=? AND parent_id=?",
                (guild_id, tok, parent),
            ).fetchone()
            if not child:
                return (0, 0)
            parent = int(child[0])
        target_id = parent

        # collect subtree ids
        ids: list[int] = []
        to_visit = [target_id]
        while to_visit:
            cur = to_visit.pop()
            ids.append(cur)
            rows = conn.execute(
                "SELECT id FROM teams WHERE guild_id=? AND parent_id=?",
                (guild_id, cur),
            ).fetchall()
            to_visit.extend(int(r[0]) for r in rows)

        # clear memberships
        q = ",".join(["?"] * len(ids))
        cur = conn.execute(
            f"DELETE FROM user_teams WHERE guild_id=? AND team_id IN ({q})",
            (guild_id, *ids),
        )
        cleared = cur.rowcount or 0

        # if subtree now has zero members, delete subtree team nodes
        exists = conn.execute(
            f"SELECT 1 FROM user_teams WHERE guild_id=? AND team_id IN ({q}) LIMIT 1",
            (guild_id, *ids),
        ).fetchone()
        removed = 0
        if not exists:
            cur = conn.execute(
                f"DELETE FROM teams WHERE guild_id=? AND id IN ({q})",
                (guild_id, *ids),
            )
            removed += cur.rowcount or 0

        # prune empty ancestors (no children, no members)
        # climb from parent of target_id
        row = conn.execute("SELECT parent_id FROM teams WHERE guild_id=? AND id=?", (guild_id, target_id)).fetchone()
        ancestor = int(row[0]) if row and row[0] is not None else None
        while ancestor is not None:
            row = conn.execute("SELECT name, parent_id FROM teams WHERE guild_id=? AND id=?", (guild_id, ancestor)).fetchone()
            if not row:
                break
            name, parent_id = str(row[0]), (int(row[1]) if row[1] is not None else None)
            if name == TEAM_ROOT_NAME:
                break
            # has children?
            c = conn.execute("SELECT 1 FROM teams WHERE guild_id=? AND parent_id=? LIMIT 1", (guild_id, ancestor)).fetchone()
            if c:
                break
            # any members under this node?
            # gather descendants for this ancestor quickly
            sub_ids: list[int] = []
            stack = [ancestor]
            while stack:
                curid = stack.pop()
                sub_ids.append(curid)
                rows = conn.execute("SELECT id FROM teams WHERE guild_id=? AND parent_id=?", (guild_id, curid)).fetchall()
                stack.extend(int(r[0]) for r in rows)
            q2 = ",".join(["?"] * len(sub_ids))
            has_member = conn.execute(
                f"SELECT 1 FROM user_teams WHERE guild_id=? AND team_id IN ({q2}) LIMIT 1",
                (guild_id, *sub_ids),
            ).fetchone()
            if has_member:
                break
            # safe to delete this ancestor
            conn.execute("DELETE FROM teams WHERE guild_id=? AND id=?", (guild_id, ancestor))
            removed += 1
            ancestor = parent_id

        return (cleared, removed)


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
    'ensure_team_path','set_user_team','clear_user_team','list_teams','list_team_members',
    'count_team_members','count_team_subtree_members',
    'get_user_team_id','get_team_path_names','set_rank_roles','get_rank_roles',
    'find_team_by_path','get_descendant_team_ids','clear_membership_subtree','delete_empty_ancestors','delete_team_path_atomic',
]

# ---------------- Inventory-based team API (new preferred implementation) ----------------

TEAM_ITEM_EMOJI = "👥"

def _normalize_path(path: str) -> str:
    tokens = [t for t in (path or "").split() if t]
    if not tokens:
        raise ValueError("팀 경로가 비어 있습니다.")
    return " ".join(tokens)


def _team_item_name(guild_id: int, path: str) -> str:
    return f"TEAM:{int(guild_id)}:{_normalize_path(path)}"


def inv_team_get_user_path(guild_id: int, user_id: int) -> str | None:
    prefix = f"TEAM:{int(guild_id)}:"
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT i.name FROM items AS i
            JOIN inventory AS inv ON inv.item_id=i.id
            WHERE inv.user_id=? AND i.name LIKE ?
            LIMIT 1
            """,
            (user_id, prefix + "%"),
        ).fetchone()
        if not row:
            return None
        name = str(row[0])
        parts = name.split(":", 2)
        return parts[2] if len(parts) >= 3 else None


def inv_team_clear_user(guild_id: int, user_id: int) -> None:
    prefix = f"TEAM:{int(guild_id)}:"
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT i.id FROM items AS i
            JOIN inventory AS inv ON inv.item_id=i.id
            WHERE inv.user_id=? AND i.name LIKE ?
            """,
            (user_id, prefix + "%"),
        ).fetchall()
        if rows:
            ids = [int(r[0]) for r in rows]
            q = ",".join(["?"] * len(ids))
            conn.execute(f"DELETE FROM inventory WHERE user_id=? AND item_id IN ({q})", (user_id, *ids))


def inv_team_set_user_path(guild_id: int, user_id: int, path: str) -> None:
    name = _team_item_name(guild_id, path)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # clear existing for this guild
        rows = conn.execute(
            "SELECT id FROM items WHERE name LIKE ?",
            (f"TEAM:{int(guild_id)}:%",),
        ).fetchall()
        if rows:
            ids = [int(r[0]) for r in rows]
            q = ",".join(["?"] * len(ids))
            conn.execute(f"DELETE FROM inventory WHERE user_id=? AND item_id IN ({q})", (user_id, *ids))
        # ensure item
        cur = conn.execute("SELECT id FROM items WHERE name=? AND emoji=?", (name, TEAM_ITEM_EMOJI))
        row = cur.fetchone()
        item_id = int(row[0]) if row else int(conn.execute("INSERT INTO items(name, emoji) VALUES(?, ?)", (name, TEAM_ITEM_EMOJI)).lastrowid)
        conn.execute(
            """
            INSERT INTO inventory(user_id, item_id, qty) VALUES(?, ?, 1)
            ON CONFLICT(user_id, item_id) DO UPDATE SET qty=1
            """,
            (user_id, item_id),
        )


def inv_team_list_members(guild_id: int, path: str) -> list[int]:
    name = _team_item_name(guild_id, path)
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM items WHERE name=? AND emoji=?", (name, TEAM_ITEM_EMOJI)).fetchone()
        if not row:
            return []
        item_id = int(row[0])
        cur = conn.execute("SELECT user_id FROM inventory WHERE item_id=? ORDER BY user_id ASC", (item_id,))
        return [int(u) for (u,) in cur.fetchall()]


def inv_team_all_user_paths(guild_id: int) -> dict[int, str]:
    res: dict[int, str] = {}
    prefix = f"TEAM:{int(guild_id)}:"
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT inv.user_id, i.name FROM inventory AS inv JOIN items AS i ON i.id=inv.item_id WHERE i.name LIKE ?",
            (prefix + "%",),
        )
        for uid, nm in cur.fetchall():
            parts = str(nm).split(":", 2)
            if len(parts) >= 3:
                res[int(uid)] = parts[2]
    return res


def inv_team_migrate_from_tables(guild_id: int) -> int:
    """Best-effort migration from legacy teams tables to inventory items.

    Returns number of users migrated. Safe to run multiple times.
    """
    migrated = 0
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT user_id, team_id FROM user_teams WHERE guild_id=?",
                (guild_id,),
            ).fetchall()
            for uid, tid in rows:
                try:
                    path_names = get_team_path_names(guild_id, int(tid))
                    if not path_names:
                        continue
                    path = " ".join(path_names)
                    inv_team_set_user_path(guild_id, int(uid), path)
                    migrated += 1
                except Exception:
                    continue
            if migrated:
                conn.execute("DELETE FROM user_teams WHERE guild_id=?", (guild_id,))
    except Exception:
        pass
    return migrated
