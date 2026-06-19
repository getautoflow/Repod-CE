"""
Gestion des groupes d'utilisateurs — PostgreSQL via SQLAlchemy Core.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from db.engine import db_conn


def create_group(name: str, description: str, color: str, created_by: str) -> dict:
    gid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with db_conn() as conn:
        conn.execute(
            text(
                "INSERT INTO groups (id, name, description, color, created_at, created_by) "
                "VALUES (:id, :name, :desc, :color, :ts, :by)"
            ),
            {"id": gid, "name": name, "desc": description, "color": color, "ts": now, "by": created_by},
        )
    return get_group(gid)


def list_groups() -> list[dict]:
    with db_conn() as conn:
        rows = conn.execute(
            text(
                "SELECT g.id, g.name, g.description, g.color, g.created_at, g.created_by, "
                "       COUNT(m.username) AS member_count "
                "FROM groups g "
                "LEFT JOIN group_members m ON m.group_id = g.id "
                "GROUP BY g.id "
                "ORDER BY g.name ASC"
            )
        ).mappings().fetchall()
    return [dict(r) for r in rows]


def get_group(group_id: str) -> dict | None:
    with db_conn() as conn:
        row = conn.execute(
            text(
                "SELECT g.id, g.name, g.description, g.color, g.created_at, g.created_by, "
                "       COUNT(m.username) AS member_count "
                "FROM groups g "
                "LEFT JOIN group_members m ON m.group_id = g.id "
                "WHERE g.id = :id "
                "GROUP BY g.id"
            ),
            {"id": group_id},
        ).mappings().fetchone()
    return dict(row) if row else None


def get_group_by_name(name: str) -> dict | None:
    with db_conn() as conn:
        row = conn.execute(
            text("SELECT id, name, description, color, created_at, created_by FROM groups WHERE name = :n"),
            {"n": name},
        ).mappings().fetchone()
    return dict(row) if row else None


def update_group(group_id: str, name: str | None = None,
                 description: str | None = None, color: str | None = None) -> dict | None:
    with db_conn() as conn:
        if name is not None:
            conn.execute(text("UPDATE groups SET name = :v WHERE id = :id"), {"v": name, "id": group_id})
        if description is not None:
            conn.execute(text("UPDATE groups SET description = :v WHERE id = :id"), {"v": description, "id": group_id})
        if color is not None:
            conn.execute(text("UPDATE groups SET color = :v WHERE id = :id"), {"v": color, "id": group_id})
    return get_group(group_id)


def delete_group(group_id: str) -> bool:
    with db_conn() as conn:
        result = conn.execute(text("DELETE FROM groups WHERE id = :id"), {"id": group_id})
    return result.rowcount > 0


def get_group_members(group_id: str) -> list[dict]:
    with db_conn() as conn:
        rows = conn.execute(
            text(
                "SELECT m.username, m.added_at, m.added_by, "
                "       u.full_name, u.email, u.role "
                "FROM group_members m "
                "LEFT JOIN users u ON u.username = m.username "
                "WHERE m.group_id = :gid "
                "ORDER BY m.username ASC"
            ),
            {"gid": group_id},
        ).mappings().fetchall()
    return [dict(r) for r in rows]


def add_member(group_id: str, username: str, added_by: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with db_conn() as conn:
        result = conn.execute(
            text(
                "INSERT INTO group_members (group_id, username, added_at, added_by) "
                "VALUES (:gid, :u, :ts, :by) ON CONFLICT DO NOTHING"
            ),
            {"gid": group_id, "u": username, "ts": now, "by": added_by},
        )
    return result.rowcount > 0


def remove_member(group_id: str, username: str) -> bool:
    with db_conn() as conn:
        result = conn.execute(
            text("DELETE FROM group_members WHERE group_id = :gid AND username = :u"),
            {"gid": group_id, "u": username},
        )
    return result.rowcount > 0


def get_user_groups(username: str) -> list[dict]:
    with db_conn() as conn:
        rows = conn.execute(
            text(
                "SELECT g.id, g.name, g.description, g.color "
                "FROM groups g "
                "JOIN group_members m ON m.group_id = g.id "
                "WHERE m.username = :u "
                "ORDER BY g.name ASC"
            ),
            {"u": username},
        ).mappings().fetchall()
    return [dict(r) for r in rows]


def get_user_group_ids(username: str) -> list[str]:
    with db_conn() as conn:
        rows = conn.execute(
            text("SELECT group_id FROM group_members WHERE username = :u"),
            {"u": username},
        ).mappings().fetchall()
    return [r["group_id"] for r in rows]
