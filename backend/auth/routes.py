"""HTTP endpoints for authentication."""
import base64
import io
import time

import segno
from flask import Blueprint, current_app, g, jsonify, request, make_response

from . import sessions as sess
from . import users as users_mod
from . import lockout
from . import audit as audit_mod
from .passwords import verify_password, hash_password


auth_bp = Blueprint("auth", __name__)

COOKIE_NAME = "snapshot_session"
DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "ZHVtbXlzYWx0$ZHVtbXloYXNoZHVtbXloYXNoZHVtbXloYXM"
)
MFA_REQUIRED_ROLES = ("admin",)


def _db():
    return current_app.config["DB_CONN"]


def _client_meta():
    return request.remote_addr, request.headers.get("User-Agent", "")[:255]


def _is_secure() -> bool:
    return request.is_secure or request.scheme == "https"


def _set_session_cookie(resp, sid: str) -> None:
    # SameSite=Lax: el cookie se envía en navegaciones GET top-level
    # (clics, bookmarks, address bar) — eso evita el bug de "me bota a
    # login al cambiar de módulo". La protección CSRF real la da el
    # header X-CSRF-Token chequeado en POST/PUT/PATCH/DELETE.
    # max_age explícito → cookie persistente hasta el TTL del server,
    # no muere al cerrar el browser.
    resp.set_cookie(
        COOKIE_NAME, sid, httponly=True, secure=_is_secure(),
        samesite="Lax", path="/",
        max_age=sess.SESSION_TTL_HOURS * 3600,
    )


def _clear_session_cookie(resp) -> None:
    resp.set_cookie(
        COOKIE_NAME, "", httponly=True, secure=_is_secure(),
        samesite="Lax", path="/", max_age=0,
    )


@auth_bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    mfa_code = payload.get("mfa_code")
    db = _db()
    ip, ua = _client_meta()

    started = time.time()
    user = users_mod.get_user_by_email(db, email) if email else None

    if user is None:
        # Equalize timing with verify
        verify_password(password, DUMMY_HASH)
        audit_mod.write_event(
            db, actor="web", event="login_fail",
            email=email, ip=ip, user_agent=ua,
            detail={"reason": "no_user"},
        )
        _equalize(started)
        return jsonify(ok=False, error="invalid credentials"), 401

    if user.status == "disabled":
        audit_mod.write_event(
            db, actor="web", event="login_fail", user_id=user.id,
            email=email, ip=ip, user_agent=ua,
            detail={"reason": "disabled"},
        )
        _equalize(started)
        return jsonify(ok=False, error="invalid credentials"), 401

    if lockout.is_locked(db, user.id):
        audit_mod.write_event(
            db, actor="web", event="login_fail", user_id=user.id,
            email=email, ip=ip, user_agent=ua,
            detail={"reason": "locked"},
        )
        _equalize(started)
        return jsonify(ok=False, error="invalid credentials"), 401

    if not verify_password(password, user.password_hash):
        lockout.record_failure(db, user.id)
        audit_mod.write_event(
            db, actor="web", event="login_fail", user_id=user.id,
            email=email, ip=ip, user_agent=ua,
            detail={"reason": "bad_pwd"},
        )
        _equalize(started)
        return jsonify(ok=False, error="invalid credentials"), 401

    # Admin must enroll MFA before getting a session.
    if user.role in MFA_REQUIRED_ROLES and not user.mfa_secret:
        audit_mod.write_event(
            db, actor="web", event="login_ok", user_id=user.id,
            email=email, ip=ip, user_agent=ua,
            detail={"step": "mfa_enroll_required"},
        )
        return jsonify(ok=True, require_mfa_enroll=True,
                       email=user.email)

    # MFA challenge if enrolled
    if user.mfa_secret:
        from . import mfa
        if not mfa_code:
            return jsonify(ok=True, require_mfa=True, email=user.email)
        sk = current_app.config["SECRET_KEY_BYTES"]
        secret = mfa.get_user_secret(db, user.id, sk)
        if not mfa.verify_totp(secret, mfa_code) and \
                not mfa.consume_backup_code(db, user.id, mfa_code):
            lockout.record_failure(db, user.id)
            audit_mod.write_event(
                db, actor="web", event="mfa_verify_fail",
                user_id=user.id, email=email, ip=ip, user_agent=ua,
            )
            return jsonify(ok=False, error="invalid credentials"), 401
        audit_mod.write_event(
            db, actor="web", event="mfa_verify_ok",
            user_id=user.id, email=email, ip=ip, user_agent=ua,
        )
        mfa_verified = True
    else:
        mfa_verified = False

    s = sess.create_session(
        db, user_id=user.id, ip=ip, user_agent=ua,
        mfa_verified=mfa_verified,
    )
    lockout.record_success(db, user.id)
    audit_mod.write_event(
        db, actor="web", event="login_ok", user_id=user.id,
        email=email, ip=ip, user_agent=ua,
    )
    resp = make_response(jsonify(
        ok=True, role=user.role, display_name=user.display_name,
        csrf_token=s.csrf_token, expires_at=s.expires_at,
    ))
    _set_session_cookie(resp, s.id)
    return resp


def _equalize(started: float, target_ms: int = 250) -> None:
    """Make all login failures take roughly the same time as a real verify."""
    elapsed = (time.time() - started) * 1000
    if elapsed < target_ms:
        time.sleep((target_ms - elapsed) / 1000.0)


@auth_bp.post("/logout")
def logout():
    s = getattr(g, "session", None)
    if s:
        sess.revoke_session(_db(), s.id)
        ip, ua = _client_meta()
        audit_mod.write_event(
            _db(), actor="web", event="logout",
            user_id=g.current_user.id, email=g.current_user.email,
            ip=ip, user_agent=ua,
        )
    resp = make_response(jsonify(ok=True))
    _clear_session_cookie(resp)
    return resp


@auth_bp.get("/csrf")
def csrf():
    s = getattr(g, "session", None)
    if not s:
        return jsonify(ok=False, error="unauthenticated"), 401
    return jsonify(ok=True, csrf_token=s.csrf_token)


@auth_bp.get("/me")
def me():
    u = getattr(g, "current_user", None)
    if not u:
        return jsonify(ok=False, error="unauthenticated"), 401
    return jsonify(ok=True, email=u.email, role=u.role,
                   display_name=u.display_name,
                   mfa_enrolled=bool(u.mfa_secret))


from .passwords import (
    validate_policy, check_history, PolicyError,
)


@auth_bp.post("/password")
def password_change():
    u = getattr(g, "current_user", None)
    if not u:
        return jsonify(ok=False, error="unauthenticated"), 401
    payload = request.get_json(silent=True) or {}
    current = payload.get("current") or ""
    new = payload.get("new") or ""
    db = _db()
    if not verify_password(current, u.password_hash):
        return jsonify(ok=False, error="current password incorrect"), 400
    try:
        validate_policy(new, email=u.email, display_name=u.display_name)
    except PolicyError as e:
        return jsonify(ok=False, error=str(e)), 400
    history = users_mod.get_password_history(db, u.id)
    try:
        check_history(new, history)
    except PolicyError as e:
        return jsonify(ok=False, error=str(e)), 400
    if verify_password(new, u.password_hash):
        return jsonify(ok=False, error="password unchanged"), 400
    new_hash = hash_password(new)
    users_mod.update_password(db, u.id, new_hash)
    ip, ua = _client_meta()
    audit_mod.write_event(
        db, actor="web", event="pwd_change", user_id=u.id,
        email=u.email, ip=ip, user_agent=ua,
    )
    return jsonify(ok=True)


from . import mfa as mfa_mod
from . import reset_tokens


@auth_bp.post("/mfa/enroll/start")
def mfa_enroll_start():
    """Stage 1 of admin enrollment.

    The admin re-supplies email+password (no session yet — login put
    them in a require_mfa_enroll state). We validate creds and return
    a fresh secret + otpauth URI. The secret is NOT persisted yet.
    """
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    db = _db()
    user = users_mod.get_user_by_email(db, email) if email else None

    if not user:
        # equalize timing with verify
        verify_password(password, DUMMY_HASH)
        return jsonify(ok=False, error="invalid credentials"), 401

    if user.status != "active" or lockout.is_locked(db, user.id):
        verify_password(password, DUMMY_HASH)
        return jsonify(ok=False, error="invalid credentials"), 401

    if not verify_password(password, user.password_hash):
        lockout.record_failure(db, user.id)
        return jsonify(ok=False, error="invalid credentials"), 401

    if user.mfa_secret:
        return jsonify(ok=False, error="already enrolled"), 400
    secret = mfa_mod.generate_totp_secret()
    otpauth_uri = mfa_mod.build_otpauth_uri(secret, email=user.email)
    qr = segno.make(otpauth_uri, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=6, border=2, dark="#000000", light="#ffffff")
    qr_data_url = "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return jsonify(
        ok=True,
        secret=secret,
        otpauth_uri=otpauth_uri,
        qr_data_url=qr_data_url,
    )


@auth_bp.post("/mfa/enroll/confirm")
def mfa_enroll_confirm():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    secret = payload.get("secret") or ""
    code = payload.get("code") or ""
    db = _db()
    user = users_mod.get_user_by_email(db, email) if email else None

    if not user:
        # equalize timing with verify
        verify_password(password, DUMMY_HASH)
        return jsonify(ok=False, error="invalid credentials"), 401

    if user.status != "active" or lockout.is_locked(db, user.id):
        verify_password(password, DUMMY_HASH)
        return jsonify(ok=False, error="invalid credentials"), 401

    if not verify_password(password, user.password_hash):
        lockout.record_failure(db, user.id)
        return jsonify(ok=False, error="invalid credentials"), 401
    if user.mfa_secret:
        return jsonify(ok=False, error="already enrolled"), 400
    if not mfa_mod.verify_totp(secret, code):
        return jsonify(ok=False, error="invalid code"), 400
    sk = current_app.config["SECRET_KEY_BYTES"]
    backup_codes = mfa_mod.enroll_totp(db, user.id, secret, sk)
    ip, ua = _client_meta()
    audit_mod.write_event(
        db, actor="web", event="mfa_enable", user_id=user.id,
        email=user.email, ip=ip, user_agent=ua,
    )
    s = sess.create_session(
        db, user_id=user.id, ip=ip, user_agent=ua, mfa_verified=True,
    )
    lockout.record_success(db, user.id)
    resp = make_response(jsonify(
        ok=True, backup_codes=backup_codes, role=user.role,
        display_name=user.display_name, csrf_token=s.csrf_token,
    ))
    _set_session_cookie(resp, s.id)
    return resp


def _send_reset_email(email: str, token: str) -> None:
    """Best-effort email send. Fails silently in tests/no SMTP."""
    cfg = current_app.config
    host = cfg.get("SMTP_HOST") or ""
    if not host:
        return
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = "snapshot-V3 — restablecer contraseña"
    msg["From"] = cfg.get("SMTP_FROM", "snapshot-v3@localhost")
    msg["To"] = email
    base = cfg.get("PUBLIC_BASE_URL", "")
    msg.set_content(
        f"Para restablecer tu contraseña abrí:\n\n"
        f"{base}/auth/reset?token={token}\n\n"
        f"El enlace expira en 1 hora."
    )
    try:
        with smtplib.SMTP(host, int(cfg.get("SMTP_PORT", 587))) as s:
            s.starttls()
            user = cfg.get("SMTP_USER")
            pwd = cfg.get("SMTP_PASSWORD")
            if user:
                s.login(user, pwd or "")
            s.send_message(msg)
    except Exception as e:
        current_app.logger.warning("reset email failed: %s", e)


@auth_bp.post("/reset-request")
def reset_request():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    db = _db()
    ip, ua = _client_meta()
    user = users_mod.get_user_by_email(db, email) if email else None
    if user and user.status == "active":
        token = reset_tokens.create_reset_token(db, user.id)
        audit_mod.write_event(
            db, actor="web", event="reset_request", user_id=user.id,
            email=email, ip=ip, user_agent=ua,
        )
        _send_reset_email(user.email, token)
    return jsonify(ok=True)


@auth_bp.post("/reset-consume")
def reset_consume():
    payload = request.get_json(silent=True) or {}
    token = payload.get("token") or ""
    new = payload.get("new_password") or ""
    db = _db()
    user_id = reset_tokens.consume_reset_token(db, token)
    if user_id is None:
        return jsonify(ok=False, error="invalid or expired token"), 400
    user = users_mod.get_user_by_id(db, user_id)
    if not user:
        return jsonify(ok=False, error="invalid token"), 400
    try:
        validate_policy(new, email=user.email, display_name=user.display_name)
    except PolicyError as e:
        return jsonify(ok=False, error=str(e)), 400
    history = users_mod.get_password_history(db, user.id)
    try:
        check_history(new, history)
    except PolicyError as e:
        return jsonify(ok=False, error=str(e)), 400
    users_mod.update_password(db, user.id, hash_password(new))
    sess.revoke_user_sessions(db, user.id)
    ip, ua = _client_meta()
    audit_mod.write_event(
        db, actor="web", event="reset_consume", user_id=user.id,
        email=user.email, ip=ip, user_agent=ua,
    )
    audit_mod.write_event(
        db, actor="web", event="pwd_change", user_id=user.id,
        email=user.email, ip=ip, user_agent=ua,
    )
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Admin user management endpoints
# ---------------------------------------------------------------------------

import secrets as _secrets

from .decorators import require_role


def _user_dict(u) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "status": u.status,
        "mfa_enrolled": bool(u.mfa_secret),
        "last_login_at": u.last_login_at,
    }


@auth_bp.get("/users")
@require_role("admin")
def list_users_route():
    rows = users_mod.list_users(_db())
    return jsonify(ok=True, users=[_user_dict(u) for u in rows])


@auth_bp.post("/users")
@require_role("admin")
def create_user_route():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    display = (payload.get("display_name") or "").strip()
    role = (payload.get("role") or "").strip()
    password = payload.get("password") or _gen_temp_password()
    if not email or not display or role not in users_mod.VALID_ROLES:
        return jsonify(ok=False, error="invalid input"), 400
    try:
        validate_policy(password, email=email, display_name=display)
    except PolicyError as e:
        return jsonify(ok=False, error=str(e)), 400
    try:
        u = users_mod.create_user(
            _db(), email=email, display_name=display,
            password_hash=hash_password(password), role=role,
        )
    except users_mod.UserExists:
        return jsonify(ok=False, error="email already exists"), 400
    ip, ua = _client_meta()
    audit_mod.write_event(
        _db(), actor="web", event="user_create",
        user_id=u.id, email=u.email, ip=ip, user_agent=ua,
        detail={"role": role, "by": g.current_user.email},
    )
    return jsonify(ok=True, user=_user_dict(u),
                   initial_password=password if not payload.get("password")
                                    else None)


@auth_bp.post("/users/<int:uid>/set-role")
@require_role("admin")
def set_role_route(uid: int):
    payload = request.get_json(silent=True) or {}
    role = (payload.get("role") or "").strip()
    if role not in users_mod.VALID_ROLES:
        return jsonify(ok=False, error="invalid role"), 400
    try:
        users_mod.set_role(_db(), uid, role)
    except users_mod.UserNotFound:
        return jsonify(ok=False, error="user not found"), 404
    audit_mod.write_event(
        _db(), actor="web", event="role_change",
        user_id=uid, ip=request.remote_addr,
        detail={"new_role": role, "by": g.current_user.email},
    )
    return jsonify(ok=True)


@auth_bp.post("/users/<int:uid>/disable")
@require_role("admin")
def disable_route(uid: int):
    try:
        users_mod.set_status(_db(), uid, "disabled")
    except users_mod.UserNotFound:
        return jsonify(ok=False, error="user not found"), 404
    sess.revoke_user_sessions(_db(), uid)
    audit_mod.write_event(
        _db(), actor="web", event="user_disable",
        user_id=uid, ip=request.remote_addr,
        detail={"by": g.current_user.email},
    )
    return jsonify(ok=True)


@auth_bp.post("/users/<int:uid>/enable")
@require_role("admin")
def enable_route(uid: int):
    try:
        users_mod.set_status(_db(), uid, "active")
    except users_mod.UserNotFound:
        return jsonify(ok=False, error="user not found"), 404
    return jsonify(ok=True)


def _gen_temp_password() -> str:
    alphabet = ("ABCDEFGHJKLMNPQRSTUVWXYZ"
                "abcdefghijkmnpqrstuvwxyz23456789")
    return "-".join(
        "".join(_secrets.choice(alphabet) for _ in range(4))
        for _ in range(4)
    )  # e.g. abCD-efGH-ijKL-mnOP


@auth_bp.post("/users/<int:uid>/reset-password")
@require_role("admin")
def admin_reset_password_route(uid: int):
    target = users_mod.get_user_by_id(_db(), uid)
    if not target:
        return jsonify(ok=False, error="user not found"), 404
    pwd = _gen_temp_password()
    users_mod.update_password(_db(), uid, hash_password(pwd))
    sess.revoke_user_sessions(_db(), uid)
    audit_mod.write_event(
        _db(), actor="web", event="pwd_change",
        user_id=uid, email=target.email, ip=request.remote_addr,
        detail={"reason": "admin_reset", "by": g.current_user.email},
    )
    return jsonify(ok=True, temp_password=pwd)


@auth_bp.post("/users/<int:uid>/revoke-sessions")
@require_role("admin")
def revoke_sessions_route(uid: int):
    sess.revoke_user_sessions(_db(), uid)
    audit_mod.write_event(
        _db(), actor="web", event="session_revoked_admin",
        user_id=uid, ip=request.remote_addr,
        detail={"by": g.current_user.email},
    )
    return jsonify(ok=True)


@auth_bp.post("/users/<int:uid>/reset-mfa")
@require_role("admin")
def reset_mfa_route(uid: int):
    target = users_mod.get_user_by_id(_db(), uid)
    if not target:
        return jsonify(ok=False, error="user not found"), 404
    mfa_mod.disable_totp(_db(), uid)
    audit_mod.write_event(
        _db(), actor="web", event="mfa_disable",
        user_id=uid, email=target.email, ip=request.remote_addr,
        detail={"by": g.current_user.email},
    )
    return jsonify(ok=True)


def register_rate_limits(app):
    """Apply Flask-Limiter rules to auth endpoints. Called from app.py
    AFTER the blueprint is registered so the view functions are bound.

    Flask-Limiter 4.x requires that the decorated function is the one
    stored in app.view_functions — simply calling limiter.limit()(fn)
    without replacing the view function entry does not enforce limits
    during the before_request middleware pass. We replace each entry
    directly so the limiter's decorator wraps the actual callable that
    Flask will invoke.
    """
    limiter = app.config.get("LIMITER")
    if not limiter:
        return
    app.view_functions["auth.login"] = limiter.limit("10/minute")(login)
    app.view_functions["auth.reset_request"] = limiter.limit("3/minute;30/hour")(reset_request)
    app.view_functions["auth.mfa_enroll_start"] = limiter.limit("5/minute")(mfa_enroll_start)
    app.view_functions["auth.mfa_enroll_confirm"] = limiter.limit("5/minute")(mfa_enroll_confirm)
