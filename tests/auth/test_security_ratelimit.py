"""Task 23: Flask-Limiter rate limits on auth endpoints."""
import pytest
import sqlite3
from flask import Flask

from backend.auth.migrations import apply_migrations
from backend.auth.users import create_user
from backend.auth.passwords import hash_password
from backend.auth import auth_bp
from backend.auth.middleware import install_auth_middleware


@pytest.fixture
def app(tmp_path):
    """Fresh Flask app with auth blueprint and Limiter wired — mirrors app.py."""
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    application = Flask(__name__)
    application.config["SECRET_KEY_BYTES"] = bytes.fromhex("0" * 64)
    conn = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    apply_migrations(conn)
    application.config["DB_CONN"] = conn

    install_auth_middleware(application)

    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri="memory://",
        default_limits=[],
    )
    limiter.init_app(application)
    application.config["LIMITER"] = limiter

    application.register_blueprint(auth_bp, url_prefix="/auth")

    from backend.auth.routes import register_rate_limits
    register_rate_limits(application)

    return application


@pytest.fixture
def client(app):
    return app.test_client()


def test_login_rate_limit(client):
    payload = {"email": "x@x.com", "password": "wrong"}
    statuses = []
    for _ in range(15):
        r = client.post(
            "/auth/login",
            json=payload,
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        statuses.append(r.status_code)
    # At least one of the last requests must be 429
    assert 429 in statuses
