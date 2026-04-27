"""TOTP MFA + backup codes."""
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import List
from urllib.parse import quote

import pyotp

from .crypto import encrypt_secret, decrypt_secret
from .passwords import hash_password, verify_password


BACKUP_CODE_COUNT = 10
BACKUP_CODE_LEN = 16
ISSUER = "snapshot-V3"


def generate_totp_secret() -> str:
    return pyotp.random_base32()  # 32 base32 chars


def build_otpauth_uri(secret: str, email: str) -> str:
    label = f"{ISSUER}:{email}"
    return (
        f"otpauth://totp/{quote(label)}"
        f"?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits=6&period=30"
    )


def verify_totp(secret: str, code: str) -> bool:
    if not code or not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def _alphanum(n: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def generate_backup_codes() -> List[str]:
    return [_alphanum(BACKUP_CODE_LEN) for _ in range(BACKUP_CODE_COUNT)]


def enroll_totp(
    conn: sqlite3.Connection,
    user_id: int,
    secret: str,
    secret_key: bytes,
) -> List[str]:
    encrypted = encrypt_secret(secret, secret_key, info=b"mfa")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE users SET mfa_secret=?, mfa_enrolled_at=?, updated_at=? "
        "WHERE id=?",
        (encrypted, now, now, user_id),
    )
    codes = generate_backup_codes()
    conn.executemany(
        "INSERT INTO mfa_backup_codes(user_id,code_hash) VALUES(?,?)",
        [(user_id, hash_password(c)) for c in codes],
    )
    conn.commit()
    return codes


def get_user_secret(
    conn: sqlite3.Connection, user_id: int, secret_key: bytes
) -> str | None:
    row = conn.execute(
        "SELECT mfa_secret FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not row or not row[0]:
        return None
    return decrypt_secret(row[0], secret_key, info=b"mfa")


def disable_totp(conn: sqlite3.Connection, user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE users SET mfa_secret=NULL, mfa_enrolled_at=NULL, "
        "updated_at=? WHERE id=?", (now, user_id)
    )
    conn.execute(
        "DELETE FROM mfa_backup_codes WHERE user_id=?", (user_id,)
    )
    conn.commit()


def consume_backup_code(
    conn: sqlite3.Connection, user_id: int, code: str
) -> bool:
    rows = conn.execute(
        "SELECT code_hash FROM mfa_backup_codes "
        "WHERE user_id=? AND consumed_at IS NULL", (user_id,)
    ).fetchall()
    for (h,) in rows:
        if verify_password(code, h):
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE mfa_backup_codes SET consumed_at=? "
                "WHERE user_id=? AND code_hash=?",
                (now, user_id, h),
            )
            conn.commit()
            return True
    return False
