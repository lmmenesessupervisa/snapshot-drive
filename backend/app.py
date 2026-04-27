"""snapshot-V3 — backend Flask.

Arranque:
    python -m backend.app
o (systemd): /usr/bin/python3 /opt/snapshot-V3/backend/app.py
"""
import logging
import logging.handlers
import os
import sqlite3
import sys
from pathlib import Path

# Permitir ejecución directa: python backend/app.py
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify  # noqa: E402
from flask_limiter import Limiter  # noqa: E402
from flask_limiter.util import get_remote_address  # noqa: E402
from flask_talisman import Talisman  # noqa: E402

from backend.auth import auth_bp  # noqa: E402
from backend.auth.middleware import install_auth_middleware  # noqa: E402
from backend.auth.migrations import apply_migrations  # noqa: E402
from backend.config import Config, load_secret_key  # noqa: E402
from backend.models.db import DB  # noqa: E402
from backend.routes.api import api_bp  # noqa: E402
from backend.routes.audit import audit_bp  # noqa: E402
from backend.routes.web import web_bp  # noqa: E402
from backend.services.snapctl import SnapctlService  # noqa: E402

def _test_mode() -> bool:
    return os.getenv("SNAPSHOT_TEST_MODE") == "1"


def _setup_logging():
    fmt = logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not _test_mode():
        Config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            Config.LOG_FILE, maxBytes=5_000_000, backupCount=5
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def create_app() -> Flask:
    _setup_logging()

    app = Flask(
        __name__,
        template_folder=str(ROOT / "frontend" / "templates"),
        static_folder=str(ROOT / "frontend" / "static"),
    )
    app.config["SECRET_KEY"] = Config.SECRET_KEY

    # Resolve DB path: SNAPSHOT_DB_PATH env (set by tests) overrides Config.DB_PATH
    db_path = Path(os.getenv("SNAPSHOT_DB_PATH") or Config.DB_PATH)
    app.config["DB_PATH"] = db_path

    db = DB(db_path)
    svc = SnapctlService(db=db, bin_path=Config.SNAPCTL_BIN, timeout=Config.SNAPCTL_TIMEOUT)
    app.config["DB"] = db
    app.config["SNAPCTL_SVC"] = svc

    # --- Auth bootstrap ---
    app.config["SECRET_KEY_BYTES"] = load_secret_key()
    auth_conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    auth_conn.execute("PRAGMA journal_mode=WAL")
    apply_migrations(auth_conn)
    app.config["DB_CONN"] = auth_conn

    install_auth_middleware(app)

    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri="memory://",
        default_limits=[],
    )
    limiter.init_app(app)
    app.config["LIMITER"] = limiter

    app.register_blueprint(auth_bp, url_prefix="/auth")

    from .auth.routes import register_rate_limits
    register_rate_limits(app)

    csp = {
        "default-src": "'self'",
        "img-src": ["'self'", "data:"],
        "style-src": ["'self'", "'unsafe-inline'", "https://cdn.tailwindcss.com"],
        "script-src": ["'self'", "'unsafe-inline'", "https://cdn.tailwindcss.com"],
        "connect-src": "'self'",
        "frame-ancestors": "'none'",
    }
    Talisman(
        app,
        content_security_policy=csp,
        force_https=False,
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        frame_options="DENY",
        referrer_policy="same-origin",
    )

    app.register_blueprint(api_bp)
    app.register_blueprint(web_bp)
    app.register_blueprint(audit_bp)

    # Expón a las plantillas si /audit está habilitado (sirve para mostrar
    # el link de navegación solo en deploys de ops).
    @app.context_processor
    def _inject_flags():
        return {"audit_enabled": Config.AUDIT_ENABLED}

    @app.errorhandler(404)
    def _404(e):
        return jsonify(ok=False, error="not found"), 404

    @app.errorhandler(500)
    def _500(e):
        logging.getLogger("app").exception("error 500: %s", e)
        return jsonify(ok=False, error="internal error"), 500

    return app


if not _test_mode():
    app = create_app()


if __name__ == "__main__":
    host = Config.HOST
    port = Config.PORT
    debug = os.getenv("FLASK_DEBUG") == "1"
    app.run(host=host, port=port, debug=debug)
