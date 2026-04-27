"""HTTP endpoints for authentication."""
import time
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
    resp.set_cookie(
        COOKIE_NAME, sid, httponly=True, secure=_is_secure(),
        samesite="Strict", path="/",
    )


def _clear_session_cookie(resp) -> None:
    resp.set_cookie(
        COOKIE_NAME, "", httponly=True, secure=_is_secure(),
        samesite="Strict", path="/", max_age=0,
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
