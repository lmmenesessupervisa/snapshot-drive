"""User CRUD operations on the `users` table."""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


VALID_ROLES = ("admin", "operator", "auditor")
VALID_STATUSES = ("active", "disabled", "locked")


class UserExists(Exception):
    pass


class UserNotFound(Exception):
    pass


@dataclass
class User:
    id: int
    email: str
    display_name: str
    password_hash: str
    role: str
    mfa_secret: Optional[str]
    mfa_enrolled_at: Optional[str]
    status: str
    failed_attempts: int
    lock_count: int
    locked_until: Optional[str]
    created_at: str
    updated_at: str
    last_login_at: Optional[str]


_COLS = (
    "id,email,display_name,password_hash,role,mfa_secret,mfa_enrolled_at,"
    "status,failed_attempts,lock_count,locked_until,created_at,updated_at,"
    "last_login_at"
)


def _row_to_user(row) -> User:
    return User(*row)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_user(
    conn: sqlite3.Connection,
    *,
    email: str,
    display_name: str,
    password_hash: str,
    role: str,
) -> User:
    if role not in VALID_ROLES:
        raise ValueError(f"invalid role: {role}")
    now = _now()
    try:
        cur = conn.execute(
            "INSERT INTO users(email,display_name,password_hash,role,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (email, display_name, password_hash, role, now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        if "email" in str(e).lower() or "UNIQUE" in str(e):
            raise UserExists(email) from e
        raise
    return get_user_by_id(conn, cur.lastrowid)


def get_user_by_email(conn, email: str) -> Optional[User]:
    row = conn.execute(
        f"SELECT {_COLS} FROM users WHERE email = ? COLLATE NOCASE",
        (email,),
    ).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_id(conn, user_id: int) -> Optional[User]:
    row = conn.execute(
        f"SELECT {_COLS} FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return _row_to_user(row) if row else None


def list_users(conn) -> list[User]:
    rows = conn.execute(
        f"SELECT {_COLS} FROM users ORDER BY status='active' DESC, email"
    ).fetchall()
    return [_row_to_user(r) for r in rows]


def set_role(conn, user_id: int, role: str) -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"invalid role: {role}")
    cur = conn.execute(
        "UPDATE users SET role=?, updated_at=? WHERE id=?",
        (role, _now(), user_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise UserNotFound(user_id)


def set_status(conn, user_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    cur = conn.execute(
        "UPDATE users SET status=?, updated_at=? WHERE id=?",
        (status, _now(), user_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise UserNotFound(user_id)


def update_password(conn, user_id: int, new_hash: str) -> None:
    cur = conn.execute(
        "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
        (new_hash, _now(), user_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise UserNotFound(user_id)
    conn.execute(
        "INSERT INTO password_history(user_id,password_hash,changed_at)"
        " VALUES (?,?,?)",
        (user_id, new_hash, _now()),
    )
    # Trim to last 5
    conn.execute("""
        DELETE FROM password_history
        WHERE user_id = ?
          AND changed_at NOT IN (
            SELECT changed_at FROM password_history
            WHERE user_id = ?
            ORDER BY changed_at DESC LIMIT 5
          )
    """, (user_id, user_id))
    conn.commit()


def get_password_history(conn, user_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT password_hash FROM password_history "
        "WHERE user_id=? ORDER BY changed_at DESC LIMIT 5",
        (user_id,),
    ).fetchall()
    return [r[0] for r in rows]
