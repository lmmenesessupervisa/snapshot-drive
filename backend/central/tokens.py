"""Issuance, hashing y verify de Bearer tokens M2M para sub-proyecto B.

Mismo patron que backend/auth/passwords.py - argon2id, jamas texto plano
en DB. Plaintext se devuelve UNA SOLA VEZ al issuance; verify hace SELECT
de todos los hashes (no se puede indexar argon2) y compara uno por uno.
A escalas <10k tokens activos por central esto es <50ms.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_HASHER = PasswordHasher()


@dataclass
class TokenInfo:
    id: int
    client_id: int
    scope: str
    label: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_plaintext() -> str:
    """32 bytes random -> 64 hex chars."""
    return secrets.token_hex(32)


def issue(conn, client_id: int, *, label: str,
          scope: str = "heartbeat:write",
          expires_at: Optional[str] = None) -> tuple[str, int]:
    """Crea un token. Devuelve (plaintext, token_id). El plaintext NO se persiste."""
    plaintext = _gen_plaintext()
    h = _HASHER.hash(plaintext)
    cur = conn.execute(
        "INSERT INTO central_tokens "
        "(token_hash, client_id, label, scope, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (h, client_id, label, scope, _now_iso(), expires_at),
    )
    conn.commit()
    return plaintext, cur.lastrowid


def verify(conn, plaintext: str) -> Optional[TokenInfo]:
    if not plaintext or len(plaintext) < 16:
        return None
    rows = conn.execute(
        "SELECT id, token_hash, client_id, scope, label, expires_at "
        "FROM central_tokens WHERE revoked_at IS NULL"
    ).fetchall()
    now = _now_iso()
    for row in rows:
        # row is sqlite3.Row - accept by index for portability with raw tuples
        token_id = row[0]
        token_hash = row[1]
        client_id = row[2]
        scope = row[3]
        label = row[4]
        expires_at = row[5]
        if expires_at and expires_at <= now:
            continue
        try:
            _HASHER.verify(token_hash, plaintext)
        except VerifyMismatchError:
            continue
        conn.execute(
            "UPDATE central_tokens SET last_used_at=? WHERE id=?",
            (now, token_id),
        )
        conn.commit()
        return TokenInfo(id=token_id, client_id=client_id, scope=scope, label=label)
    return None


def revoke(conn, token_id: int) -> None:
    conn.execute(
        "UPDATE central_tokens SET revoked_at=? WHERE id=?",
        (_now_iso(), token_id),
    )
    conn.commit()


def list_active(conn, client_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, label, scope, created_at, last_used_at, expires_at "
        "FROM central_tokens "
        "WHERE client_id=? AND revoked_at IS NULL "
        "ORDER BY created_at DESC",
        (client_id,),
    ).fetchall()
    return [
        {"id": r[0], "label": r[1], "scope": r[2],
         "created_at": r[3], "last_used_at": r[4], "expires_at": r[5]}
        for r in rows
    ]
