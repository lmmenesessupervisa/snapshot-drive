"""Las 6 tablas nuevas deben crearse al boot, en cualquier modo."""
from backend.models.db import DB


def test_creates_central_tables(tmp_path):
    db_path = tmp_path / "t.db"
    DB(db_path)  # constructor llama _init_schema
    import sqlite3
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    expected = {
        "clients",
        "targets",
        "central_tokens",
        "central_events",
        "central_user_perms",
        "central_queue",
    }
    assert expected.issubset(names), f"falta(n): {expected - names}"


def test_indexes_present(tmp_path):
    db_path = tmp_path / "t.db"
    DB(db_path)
    import sqlite3
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    names = {r[0] for r in rows}
    for needed in (
        "idx_targets_client",
        "idx_targets_silent",
        "idx_tokens_client",
        "idx_events_target_ts",
        "idx_events_client_ts",
        "idx_queue_due",
    ):
        assert needed in names, f"falta índice {needed}"
