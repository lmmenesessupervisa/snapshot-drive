def test_x_frame_options(client):
    r = client.get("/api/health")
    assert r.headers.get("X-Frame-Options") == "DENY"


def test_no_sniff(client):
    r = client.get("/api/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_csp_present(client):
    r = client.get("/api/health")
    assert "default-src" in r.headers.get("Content-Security-Policy", "")
