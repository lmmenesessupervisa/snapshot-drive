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


def _login_as(client, conn, role: str):
    from tests.auth.helpers import create_user_and_login
    return create_user_and_login(client, conn, role=role)


def _hdr(csrf):
    return {"X-CSRF-Token": csrf}


@pytest.mark.parametrize("role,endpoint,method,expected", [
    ("auditor", "/api/admin/clients", "GET", 200),
    ("auditor", "/api/admin/clients", "POST", 403),
    ("operator", "/api/admin/clients", "POST", 200),
    ("auditor", "/api/admin/tokens/9999", "DELETE", 403),
    ("operator", "/api/admin/tokens/9999", "DELETE", 404),
])
def test_admin_perm_matrix(client, conn, role, endpoint, method, expected):
    _, _, csrf = _login_as(client, conn, role)
    if method == "GET":
        r = client.get(endpoint)
    elif method == "POST":
        r = client.post(endpoint, json={"proyecto": "test"}, headers=_hdr(csrf))
    else:
        r = client.delete(endpoint, headers=_hdr(csrf))
    assert r.status_code == expected, r.get_data(as_text=True)


def test_create_then_list_then_delete_client(client, conn):
    _, _, csrf = _login_as(client, conn, "operator")
    r = client.post("/api/admin/clients",
                    json={"proyecto": "alpha", "organizacion": "Acme"},
                    headers=_hdr(csrf))
    assert r.status_code == 200, r.get_data(as_text=True)
    cid = r.get_json()["data"]["id"]
    r = client.get("/api/admin/clients")
    proyectos = [c["proyecto"] for c in r.get_json()["data"]]
    assert "alpha" in proyectos
    r = client.delete(f"/api/admin/clients/{cid}", headers=_hdr(csrf))
    assert r.status_code == 200


def test_issue_token_returns_plaintext_once(client, conn):
    _, _, csrf = _login_as(client, conn, "operator")
    r = client.post("/api/admin/clients", json={"proyecto": "alpha"},
                    headers=_hdr(csrf))
    cid = r.get_json()["data"]["id"]
    r = client.post(f"/api/admin/clients/{cid}/tokens",
                    json={"label": "web01"}, headers=_hdr(csrf))
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()["data"]
    assert "plaintext" in body and len(body["plaintext"]) >= 32
    assert "token_id" in body
