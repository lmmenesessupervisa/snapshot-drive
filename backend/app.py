"""snapshot-V3 — backend Flask.

Arranque:
    python -m backend.app
o (systemd): /usr/bin/python3 /opt/snapshot-V3/backend/app.py
"""
import logging
import logging.handlers
import os
import sys
from pathlib import Path

# Permitir ejecución directa: python backend/app.py
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify  # noqa: E402

from backend.config import Config  # noqa: E402
from backend.models.db import DB  # noqa: E402
from backend.routes.api import api_bp  # noqa: E402
from backend.routes.web import web_bp  # noqa: E402
from backend.services.snapctl import SnapctlService  # noqa: E402


def _setup_logging():
    Config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

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

    db = DB(Config.DB_PATH)
    svc = SnapctlService(db=db, bin_path=Config.SNAPCTL_BIN, timeout=Config.SNAPCTL_TIMEOUT)
    app.config["DB"] = db
    app.config["SNAPCTL_SVC"] = svc

    app.register_blueprint(api_bp)
    app.register_blueprint(web_bp)

    @app.errorhandler(404)
    def _404(e):
        return jsonify(ok=False, error="not found"), 404

    @app.errorhandler(500)
    def _500(e):
        logging.getLogger("app").exception("error 500: %s", e)
        return jsonify(ok=False, error="internal error"), 500

    return app


app = create_app()


if __name__ == "__main__":
    host = Config.HOST
    port = Config.PORT
    debug = os.getenv("FLASK_DEBUG") == "1"
    app.run(host=host, port=port, debug=debug)
