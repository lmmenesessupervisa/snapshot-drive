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


def test_clients_page_renders_for_operator(client, conn):
    from tests.auth.helpers import create_user_and_login
    create_user_and_login(client, conn, role="operator")
    r = client.get("/dashboard-central/clients")
    assert r.status_code == 200
    assert b"Crear cliente" in r.data


def test_tokens_page_renders_for_auditor_without_emit_button(client, conn):
    from tests.auth.helpers import create_user_and_login
    create_user_and_login(client, conn, role="auditor")
    cid = m.create_client(conn, proyecto="x")
    r = client.get(f"/dashboard-central/clients/{cid}/tokens")
    assert r.status_code == 200
    assert b"Emitir token" not in r.data
