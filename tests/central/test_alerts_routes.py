import pytest


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


def test_list_alerts_requires_auth(client):
    r = client.get("/api/admin/alerts")
    assert r.status_code in (401, 403)


def test_list_alerts_for_auditor_returns_active(client, conn):
    from tests.auth.helpers import create_user_and_login
    from backend.central import models as m
    from backend.central.alerts import store as st
    cid = m.create_client(conn, proyecto="alpha")
    st.fire(conn, type_="folder_missing", client_id=cid, target_id=None,
            severity="warning", detail={})
    create_user_and_login(client, conn, role="auditor")
    r = client.get("/api/admin/alerts?active=1")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert len(data) == 1
    assert data[0]["type"] == "folder_missing"


def test_acknowledge_requires_alerts_configure(client, conn):
    from tests.auth.helpers import create_user_and_login
    from backend.central import models as m
    from backend.central.alerts import store as st
    cid = m.create_client(conn, proyecto="alpha")
    a = st.fire(conn, type_="backup_shrink", client_id=cid, target_id=None,
                severity="critical", detail={})
    _, _, csrf = create_user_and_login(client, conn, role="auditor")
    r = client.post(f"/api/admin/alerts/{a['id']}/acknowledge",
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403


def test_operator_can_acknowledge(client, conn):
    from tests.auth.helpers import create_user_and_login
    from backend.central import models as m
    from backend.central.alerts import store as st
    cid = m.create_client(conn, proyecto="alpha")
    a = st.fire(conn, type_="backup_shrink", client_id=cid, target_id=None,
                severity="critical", detail={})
    _, _, csrf = create_user_and_login(client, conn, role="operator")
    r = client.post(f"/api/admin/alerts/{a['id']}/acknowledge",
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.get_data(as_text=True)
    row = conn.execute(
        "SELECT resolved_at FROM central_alerts WHERE id=?", (a["id"],)
    ).fetchone()
    assert row[0] is not None


def test_get_config_returns_thresholds(client, conn):
    from tests.auth.helpers import create_user_and_login
    create_user_and_login(client, conn, role="auditor")
    r = client.get("/api/admin/alerts/config")
    assert r.status_code == 200
    cfg = r.get_json()["data"]
    assert "no_heartbeat_hours" in cfg
    assert "shrink_pct" in cfg
