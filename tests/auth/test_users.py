import pytest
import sqlite3

from backend.auth.migrations import apply_migrations
from backend.auth.users import (
    create_user, get_user_by_email, get_user_by_id,
    list_users, set_role, set_status, UserExists, UserNotFound,
)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    apply_migrations(c)
    return c


def test_create_and_get(conn):
    u = create_user(conn, email="a@b.c", display_name="Alice",
                    password_hash="$argon2id$x", role="admin")
    assert u.id > 0
    assert u.email == "a@b.c"
    assert u.role == "admin"
    fetched = get_user_by_email(conn, "a@b.c")
    assert fetched.id == u.id
    fetched2 = get_user_by_id(conn, u.id)
    assert fetched2.email == "a@b.c"


def test_email_unique_case_insensitive(conn):
    create_user(conn, email="A@B.c", display_name="A",
                password_hash="$x$", role="operator")
    with pytest.raises(UserExists):
        create_user(conn, email="a@b.C", display_name="A2",
                    password_hash="$y$", role="auditor")


def test_get_missing_returns_none(conn):
    assert get_user_by_email(conn, "missing@x.com") is None
    assert get_user_by_id(conn, 99999) is None


def test_list_returns_active_first(conn):
    create_user(conn, email="a@x.com", display_name="A",
                password_hash="$1$", role="admin")
    create_user(conn, email="b@x.com", display_name="B",
                password_hash="$2$", role="auditor")
    rows = list_users(conn)
    assert {r.email for r in rows} == {"a@x.com", "b@x.com"}


def test_set_role(conn):
    u = create_user(conn, email="a@x.com", display_name="A",
                    password_hash="$1$", role="operator")
    set_role(conn, u.id, "admin")
    assert get_user_by_id(conn, u.id).role == "admin"


def test_set_role_invalid(conn):
    u = create_user(conn, email="a@x.com", display_name="A",
                    password_hash="$1$", role="operator")
    with pytest.raises(ValueError):
        set_role(conn, u.id, "superuser")


def test_set_status(conn):
    u = create_user(conn, email="a@x.com", display_name="A",
                    password_hash="$1$", role="operator")
    set_status(conn, u.id, "disabled")
    assert get_user_by_id(conn, u.id).status == "disabled"


def test_set_role_missing_user(conn):
    with pytest.raises(UserNotFound):
        set_role(conn, 99999, "admin")
