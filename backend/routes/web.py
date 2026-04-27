"""Rutas HTML (sirve el dashboard)."""
from functools import wraps

from flask import Blueprint, g, redirect, render_template

web_bp = Blueprint("web", __name__)


# ---------------------------------------------------------------------------
# Login-redirect helpers
# ---------------------------------------------------------------------------

def web_require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "current_user", None):
            return redirect("/auth/login")
        return view(*args, **kwargs)
    return wrapped


def web_require_role(role):
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            u = getattr(g, "current_user", None)
            if not u:
                return redirect("/auth/login")
            if u.role != role:
                return redirect("/")
            return view(*args, **kwargs)
        return wrapped
    return deco


def web_require_any_role(*roles):
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            u = getattr(g, "current_user", None)
            if not u:
                return redirect("/auth/login")
            if u.role not in roles:
                return redirect("/")
            return view(*args, **kwargs)
        return wrapped
    return deco


# ---------------------------------------------------------------------------
# HTML routes
# ---------------------------------------------------------------------------

@web_bp.get("/")
@web_require_login
def index():
    return render_template("index.html", page="dashboard", current_user=g.current_user)


@web_bp.get("/snapshots")
@web_require_login
def snapshots():
    return render_template("snapshots.html", page="snapshots", current_user=g.current_user)


@web_bp.get("/logs")
@web_require_any_role("admin", "operator")
def logs():
    return render_template("logs.html", page="logs", current_user=g.current_user)


@web_bp.get("/settings")
@web_require_role("admin")
def settings():
    return render_template("settings.html", page="settings", current_user=g.current_user)


# ---------------------------------------------------------------------------
# Login page stub (template created in Task 28)
# ---------------------------------------------------------------------------

@web_bp.get("/auth/login")
def login_page():
    try:
        return render_template("auth/login.html")
    except Exception:
        # Fallback during transition (Task 28 creates the template)
        return '<form method="POST" action="/auth/login">login here</form>', 200
