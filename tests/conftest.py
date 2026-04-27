import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def app(tmp_db_path, monkeypatch):
    monkeypatch.setenv("SNAPSHOT_DB_PATH", tmp_db_path)
    monkeypatch.setenv("SNAPSHOT_SECRET_KEY", "0" * 64)
    monkeypatch.setenv("SNAPSHOT_TEST_MODE", "1")
    from backend.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    from backend.models.db import DB
    return DB(app.config["DB_PATH"])
