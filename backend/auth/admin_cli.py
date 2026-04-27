"""Admin CLI invoked by `snapctl admin <subcmd>`.

Runs as root with no auth required. Reuses the auth service modules
so behavior is identical to the web UI's admin actions.
"""
import argparse
import os
import secrets
import sqlite3
import sys

from . import users as users_mod
from . import sessions as sess
from . import mfa as mfa_mod
from . import audit as audit_mod
from .migrations import apply_migrations
from .passwords import hash_password


DEFAULT_DB = "/var/lib/snapshot-v3/snapshot.db"


def _open_db() -> sqlite3.Connection:
    db_path = os.environ.get("SNAPSHOT_DB_PATH", DEFAULT_DB)
    conn = sqlite3.connect(db_path, isolation_level=None)
    apply_migrations(conn)
    return conn


def _gen_password() -> str:
    alphabet = ("ABCDEFGHJKLMNPQRSTUVWXYZ"
                "abcdefghijkmnpqrstuvwxyz23456789")
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(4)
    )


def _print_users(rows):
    print(f"{'ID':>4} {'EMAIL':<32} {'ROLE':<10} {'MFA':<4} {'STATUS':<10} LAST_LOGIN")
    for u in rows:
        mfa = "yes" if u.mfa_secret else "no"
        last = u.last_login_at or "-"
        print(f"{u.id:>4} {u.email:<32} {u.role:<10} {mfa:<4} {u.status:<10} {last}")


def cmd_list(_args):
    db = _open_db()
    _print_users(users_mod.list_users(db))


def cmd_create(args):
    db = _open_db()
    pwd = args.password or _gen_password()
    try:
        u = users_mod.create_user(
            db, email=args.email, display_name=args.display or args.email,
            password_hash=hash_password(pwd), role=args.role,
        )
    except users_mod.UserExists:
        print(f"error: user {args.email} already exists", file=sys.stderr)
        return 1
    audit_mod.write_event(
        db, actor="cli", event="user_create", user_id=u.id,
        email=u.email, detail={"role": args.role},
    )
    print(f"created: {u.email} ({u.role})")
    if not args.password:
        print(f"initial password: {pwd}")
        print("ANOTALA — no se vuelve a mostrar.")


def cmd_set_role(args):
    db = _open_db()
    u = users_mod.get_user_by_email(db, args.email)
    if not u:
        print(f"error: no user {args.email}", file=sys.stderr)
        return 1
    users_mod.set_role(db, u.id, args.role)
    audit_mod.write_event(
        db, actor="cli", event="role_change", user_id=u.id,
        email=u.email, detail={"new_role": args.role},
    )
    print(f"ok: {args.email} -> {args.role}")


def cmd_reset_password(args):
    db = _open_db()
    u = users_mod.get_user_by_email(db, args.email)
    if not u:
        print(f"error: no user {args.email}", file=sys.stderr)
        return 1
    pwd = _gen_password()
    users_mod.update_password(db, u.id, hash_password(pwd))
    sess.revoke_user_sessions(db, u.id)
    audit_mod.write_event(
        db, actor="cli", event="pwd_change", user_id=u.id,
        email=u.email, detail={"reason": "cli_reset"},
    )
    print(f"new password for {args.email}: {pwd}")


def cmd_disable(args):
    db = _open_db()
    u = users_mod.get_user_by_email(db, args.email)
    if not u:
        return 1
    users_mod.set_status(db, u.id, "disabled")
    sess.revoke_user_sessions(db, u.id)
    audit_mod.write_event(
        db, actor="cli", event="user_disable", user_id=u.id, email=u.email,
    )
    print(f"disabled: {args.email}")


def cmd_enable(args):
    db = _open_db()
    u = users_mod.get_user_by_email(db, args.email)
    if not u:
        return 1
    users_mod.set_status(db, u.id, "active")
    print(f"enabled: {args.email}")


def cmd_revoke(args):
    db = _open_db()
    u = users_mod.get_user_by_email(db, args.email)
    if not u:
        return 1
    sess.revoke_user_sessions(db, u.id)
    print(f"sessions revoked for {args.email}")


def cmd_reset_mfa(args):
    db = _open_db()
    u = users_mod.get_user_by_email(db, args.email)
    if not u:
        return 1
    mfa_mod.disable_totp(db, u.id)
    audit_mod.write_event(
        db, actor="cli", event="mfa_disable", user_id=u.id, email=u.email,
    )
    print(f"mfa reset for {args.email}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="snapctl admin")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("list").set_defaults(fn=cmd_list)

    c = sp.add_parser("create")
    c.add_argument("--email", required=True)
    c.add_argument("--display")
    c.add_argument("--role", choices=("admin", "operator", "auditor"),
                   required=True)
    c.add_argument("--password")
    c.set_defaults(fn=cmd_create)

    c = sp.add_parser("set-role")
    c.add_argument("--email", required=True)
    c.add_argument("--role", choices=("admin", "operator", "auditor"),
                   required=True)
    c.set_defaults(fn=cmd_set_role)

    for name, fn in [("reset-password", cmd_reset_password),
                     ("disable", cmd_disable),
                     ("enable", cmd_enable),
                     ("revoke-sessions", cmd_revoke),
                     ("reset-mfa", cmd_reset_mfa)]:
        c = sp.add_parser(name)
        c.add_argument("--email", required=True)
        c.set_defaults(fn=fn)

    args = p.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
