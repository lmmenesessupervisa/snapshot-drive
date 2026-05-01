"""Rutas HTML (sirve el dashboard)."""
from functools import wraps

from flask import Blueprint, g, redirect, render_template

from ..config import Config

web_bp = Blueprint("web", __name__)


def _is_central() -> bool:
    return Config.MODE == "central"


def central_only(view):
    """Solo accesible si MODE=central. Si no, 404."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_central():
            from flask import abort
            abort(404)
        return view(*args, **kwargs)
    return wrapped


def client_only(view):
    """Solo accesible si MODE=client. En central redirige a auditoría —
    los módulos del cliente local no aplican al operador del central."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if _is_central():
            return redirect("/audit/")
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Template context processor — injects current_user + csrf_token into every
# template rendered by this blueprint (and app-wide via app_context_processor).
# ---------------------------------------------------------------------------

@web_bp.app_context_processor
def _inject_auth_ctx():
    return {
        "current_user": getattr(g, "current_user", None),
        "csrf_token": (g.session.csrf_token
                       if getattr(g, "session", None) else None),
    }


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
    if _is_central():
        return redirect("/audit/")
    return render_template("index.html", page="dashboard", current_user=g.current_user)


@web_bp.get("/snapshots")
@web_require_login
@client_only
def snapshots():
    return render_template("snapshots.html", page="snapshots", current_user=g.current_user)


@web_bp.get("/logs")
@web_require_any_role("admin", "operator")
@client_only
def logs():
    return render_template("logs.html", page="logs", current_user=g.current_user)


@web_bp.get("/settings")
@web_require_role("admin")
@client_only
def settings():
    return render_template("settings.html", page="settings", current_user=g.current_user)


# ---------------------------------------------------------------------------
# Central admin pages (solo MODE=central)
# ---------------------------------------------------------------------------

@web_bp.get("/central/drive")
@web_require_role("admin")
@central_only
def central_drive_page():
    """Configuración Drive del central: Device Flow + AUDIT_REMOTE_PATH."""
    return render_template(
        "central/drive.html",
        page="central-drive",
        current_user=g.current_user,
    )


# ---------------------------------------------------------------------------
# Auth pages (public — no login required)
# ---------------------------------------------------------------------------

@web_bp.get("/auth/login")
def login_page():
    return render_template("auth/login.html")


@web_bp.get("/auth/mfa-enroll")
def mfa_enroll_page():
    return render_template("auth/mfa_enroll.html")


@web_bp.get("/auth/reset-request")
def reset_request_page():
    return render_template("auth/password_reset_request.html")


@web_bp.get("/auth/reset")
def reset_consume_page():
    return render_template("auth/password_reset_consume.html")


@web_bp.get("/auth/change-password")
@web_require_login
def change_password_page():
    return render_template("auth/change_password.html")


# ---------------------------------------------------------------------------
# Admin pages
# ---------------------------------------------------------------------------

@web_bp.get("/users")
@web_require_role("admin")
def users_page():
    return render_template("users.html", page="users")
