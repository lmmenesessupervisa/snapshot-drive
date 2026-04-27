import json
import sqlite3

from backend.auth.audit import write_event
from backend.auth.migrations import apply_migrations


def _conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    apply_migrations(c)
    return c


def test_write_basic_event(tmp_path):
    c = _conn(tmp_path)
    write_event(c, actor="web", event="login_ok",
                user_id=1, email="a@b.c", ip="1.2.3.4",
                user_agent="ua", detail={"foo": "bar"})
    row = c.execute(
        "SELECT actor, event, user_id, email, ip, user_agent, detail "
        "FROM audit_auth"
    ).fetchone()
    assert row[0] == "web"
    assert row[1] == "login_ok"
    assert row[2] == 1
    assert row[3] == "a@b.c"
    assert row[4] == "1.2.3.4"
    assert row[5] == "ua"
    assert json.loads(row[6]) == {"foo": "bar"}


def test_write_with_no_user(tmp_path):
    c = _conn(tmp_path)
    write_event(c, actor="cli", event="user_create", email="new@x.com")
    row = c.execute("SELECT user_id, email FROM audit_auth").fetchone()
    assert row[0] is None
    assert row[1] == "new@x.com"
