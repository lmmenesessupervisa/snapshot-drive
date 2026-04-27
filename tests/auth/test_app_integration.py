import pytest


def test_create_app_smoke(app):
    assert app is not None


def test_health_endpoint_works_without_auth(client):
    r = client.get("/api/health")
    assert r.status_code == 200


def test_archive_create_unauth(client):
    r = client.post("/api/archive/create",
                    headers={"X-CSRF-Token": "x"})
    assert r.status_code in (401, 403)
