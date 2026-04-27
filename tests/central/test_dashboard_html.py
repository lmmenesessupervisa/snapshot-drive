import pytest
from backend.central import models as m


@pytest.fixture
def central_app(monkeypatch, tmp_path):
    monkeypatch.setenv("MODE", "central")
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SNAPSHOT_SECRET_KEY", "0" * 64)
    monkeypatch.setenv("SNAPSHOT_TEST_MODE", "1")
    import importlib, backend.config, backend.app
    importlib.reload(backend.config)
    importlib.reload(backend.app)
    from backend.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(central_app):
    return central_app.test_client()


@pytest.fixture
def conn(central_app):
    return central_app.config["DB_CONN"]


def test_dashboard_requires_auth(client):
    r = client.get("/dashboard-central")
    assert r.status_code in (302, 401)


def test_dashboard_renders_for_auditor(client, conn):
    from tests.auth.helpers import create_user_and_login
    create_user_and_login(client, conn, role="auditor")
    m.create_client(conn, proyecto="alpha")
    r = client.get("/dashboard-central")
    assert r.status_code == 200
    assert b"alpha" in r.data
    assert b"Dashboard central" in r.data
