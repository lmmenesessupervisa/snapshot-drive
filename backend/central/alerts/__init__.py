"""Central alerts subsystem: detection rules, persistence, dispatch.

Public API re-exported here for convenience; sub-modules are the real home:
- store:    DB CRUD for central_alerts rows
- rules:    pure detection logic invoked at heartbeat time
- sweep:    periodic detection (no_heartbeat)
- dispatch: notification dispatcher (email + webhook)
- routes:   alerts_bp Flask blueprint
"""
from .store import (
    fire, resolve, resolve_active_by_key, acknowledge,
    list_active, list_recent, get_by_id, count_active_critical,
    mark_notified,
)
from .routes import alerts_bp
from . import sweep  # noqa: F401 — re-exported for cli

__all__ = [
    "fire", "resolve", "resolve_active_by_key", "acknowledge",
    "list_active", "list_recent", "get_by_id", "count_active_critical",
    "mark_notified", "alerts_bp", "sweep",
]
