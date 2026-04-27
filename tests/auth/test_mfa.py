import pytest
import pyotp
import sqlite3

from backend.auth.migrations import apply_migrations
from backend.auth.users import create_user
from backend.auth.mfa import (
    generate_totp_secret, build_otpauth_uri, verify_totp,
    generate_backup_codes, enroll_totp, consume_backup_code,
    BACKUP_CODE_COUNT, BACKUP_CODE_LEN,
)


SECRET_KEY = bytes.fromhex("0" * 64)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    apply_migrations(c)
    return c


@pytest.fixture
def uid(conn):
    return create_user(conn, email="a@b.c", display_name="A",
                       password_hash="$x$", role="admin").id


def test_generate_secret_format():
    s = generate_totp_secret()
    assert len(s) == 32
    # base32: A-Z, 2-7
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in s)


def test_otpauth_uri():
    uri = build_otpauth_uri("JBSWY3DPEHPK3PXP", email="a@b.c")
    assert uri.startswith("otpauth://totp/")
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    assert "a%40b.c" in uri or "a@b.c" in uri


def test_verify_correct_code():
    s = generate_totp_secret()
    code = pyotp.TOTP(s).now()
    assert verify_totp(s, code) is True


def test_verify_wrong_code():
    s = generate_totp_secret()
    assert verify_totp(s, "000000") is False


def test_backup_codes_format():
    codes = generate_backup_codes()
    assert len(codes) == BACKUP_CODE_COUNT
    for c in codes:
        assert len(c) == BACKUP_CODE_LEN
        assert c.isalnum()


def test_enroll_persists_secret(conn, uid):
    secret = generate_totp_secret()
    codes = enroll_totp(conn, uid, secret, SECRET_KEY)
    assert len(codes) == BACKUP_CODE_COUNT
    row = conn.execute(
        "SELECT mfa_secret, mfa_enrolled_at FROM users WHERE id=?", (uid,)
    ).fetchone()
    assert row[0] is not None
    assert row[1] is not None


def test_consume_backup_code_one_shot(conn, uid):
    secret = generate_totp_secret()
    codes = enroll_totp(conn, uid, secret, SECRET_KEY)
    assert consume_backup_code(conn, uid, codes[0]) is True
    # Same code rejected second time
    assert consume_backup_code(conn, uid, codes[0]) is False


def test_consume_invalid_backup_code(conn, uid):
    secret = generate_totp_secret()
    enroll_totp(conn, uid, secret, SECRET_KEY)
    assert consume_backup_code(conn, uid, "AAAAAAAAAAAAAAAA") is False
