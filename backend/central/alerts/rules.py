"""Pure detection rules — no DB, no IO. Returns descriptors that
store.fire() will persist."""
from __future__ import annotations

from typing import Optional


def evaluate_heartbeat(payload: dict, *, prev_size_bytes: Optional[int],
                       thresholds: dict) -> list[dict]:
    """Returns a list of {type, severity, detail} dicts to fire.

    Side-effect free: caller (apply_heartbeat) persists via store.fire().
    """
    out: list[dict] = []
    host_meta = payload.get("host_meta") or {}
    missing = host_meta.get("missing_paths") or []
    if missing:
        out.append({
            "type": "folder_missing",
            "severity": "warning",
            "detail": {"missing_paths": list(missing)},
        })

    new_size = (payload.get("totals") or {}).get("size_bytes") or 0
    if prev_size_bytes and prev_size_bytes > 0:
        pct = round(100 * (prev_size_bytes - new_size) / prev_size_bytes, 1)
        if pct >= thresholds.get("shrink_pct", 20):
            sev = "critical" if pct >= 50 else "warning"
            out.append({
                "type": "backup_shrink",
                "severity": sev,
                "detail": {
                    "prev_bytes": prev_size_bytes,
                    "new_bytes": new_size,
                    "pct": pct,
                },
            })
    return out


def resolves_keys(payload: dict) -> set[str]:
    """Which alert types should auto-resolve given this heartbeat.

    backup_shrink never auto-resolves (admin must acknowledge).
    """
    out = {"no_heartbeat"}                  # any heartbeat clears no_heartbeat
    host_meta = payload.get("host_meta") or {}
    if not (host_meta.get("missing_paths") or []):
        out.add("folder_missing")
    return out
