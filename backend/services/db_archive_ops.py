"""Operaciones para el backup de bases de datos (DB).

Mismo patrón que `archive_ops.py`: el motor real vive en
`snapctl db-archive*`; este módulo solo orquesta subprocess + parseo
JSON para entregar al frontend lo que necesita.

Endpoints que consumen esto:
- GET  /api/db-archive/summary        → summary()
- POST /api/db-archive/create         → create(engines=[...])
- POST /api/db-archive/check-connection → check_connection(engine, password=None)
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from typing import Any

from ..config import Config
from . import archive_config

log = logging.getLogger(__name__)


class DbArchiveOpError(Exception):
    pass


_VALID_ENGINES = ("postgres", "mysql", "mongo")
_ENGINE_RE = re.compile(r"^(postgres|mysql|mongo)$")
# servidor_<dbname>_<YYYYMMDD>_<HHMMSS>.<ext>
_FNAME_RE = re.compile(
    r"^servidor_(?P<dbname>[A-Za-z0-9_.\-]+)_(?P<ts>\d{8}_\d{6})\."
    r"(?P<ext>(?:sql|archive|dump)\.zst(?:\.enc|\.age)?)$"
)


def _ts_iso(ts: str) -> str:
    if len(ts) != 15:
        return ""
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:{ts[13:15]}Z"


def _parsed_targets() -> list[dict]:
    """Parsea DB_BACKUP_TARGETS y devuelve [{engine, dbname, target}]."""
    cfg = archive_config.get_db_config()
    raw = (cfg.get("targets") or "").strip()
    out = []
    for tok in raw.split():
        if ":" not in tok:
            continue
        engine, dbname = tok.split(":", 1)
        if engine in _VALID_ENGINES and dbname:
            out.append({"engine": engine, "dbname": dbname, "target": tok})
    return out


def _configured_engines() -> list[str]:
    """Engines que están listados en DB_BACKUP_TARGETS (de-dup, orden estable)."""
    seen, out = set(), []
    for t in _parsed_targets():
        if t["engine"] not in seen:
            seen.add(t["engine"])
            out.append(t["engine"])
    return out


# ---------------- summary ----------------

def summary() -> dict:
    """Estado del backup DB para el dashboard.

    Devuelve:
      configured_engines: ["postgres", "mysql", ...]
      targets: [{engine, dbname, target}]
      last_per_engine: {postgres: {ts, ts_iso, size_bytes, path, dbname}, ...}
      last_overall_ts: "YYYY-MM-DDT…"  (ISO del más reciente entre todos)
      total_dumps: int
      total_size_bytes: int
    """
    targets = _parsed_targets()
    engines = []
    for t in targets:
        if t["engine"] not in engines:
            engines.append(t["engine"])

    bin_path = str(Config.SNAPCTL_BIN)
    items: list[dict] = []
    if engines:
        try:
            r = subprocess.run(
                [bin_path, "db-archive-list", "--json"],
                capture_output=True, text=True,
                timeout=60, check=False,
            )
            if r.returncode == 0:
                items = json.loads(r.stdout or "[]")
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            log.warning("db-archive-list failed: %s", e)

    # rclone lsjson pone los paths relativos al base. Para sacar engine y
    # dbname leemos los segments del path: <engine>/<dbname>/Y/M/D/<file>.
    last_per_engine: dict[str, dict] = {}
    total_size = 0
    last_overall = ""
    for it in items:
        path = it.get("Path") or ""
        parts = path.split("/")
        if len(parts) < 6:
            continue
        engine = parts[0]
        dbname = parts[1]
        fname = parts[-1]
        m = _FNAME_RE.match(fname)
        if not m:
            continue
        ts = m.group("ts")
        ts_iso = _ts_iso(ts)
        size = int(it.get("Size") or 0)
        total_size += size
        if ts_iso > last_overall:
            last_overall = ts_iso
        prev = last_per_engine.get(engine)
        if prev is None or ts_iso > prev["ts_iso"]:
            last_per_engine[engine] = {
                "ts": ts,
                "ts_iso": ts_iso,
                "size_bytes": size,
                "path": path,
                "dbname": dbname,
            }

    return {
        "configured_engines": engines,
        "targets": targets,
        "last_per_engine": last_per_engine,
        "last_overall_ts": last_overall or None,
        "total_dumps": len(items),
        "total_size_bytes": total_size,
    }


# ---------------- create ----------------

def create(engines: list[str] | None = None,
           targets: list[str] | None = None) -> dict:
    """Dispara `snapctl db-archive` síncrono. Filtra por engines y/o
    targets si se pasan; sin filtros corre todos los configurados."""
    if engines:
        for e in engines:
            if e not in _VALID_ENGINES:
                raise DbArchiveOpError(f"engine no válido: {e}")
    if targets:
        for t in targets:
            if ":" not in t or t.split(":")[0] not in _VALID_ENGINES:
                raise DbArchiveOpError(f"target malformado: {t}")

    bin_path = str(Config.SNAPCTL_BIN)
    args = [bin_path, "db-archive"]
    for e in (engines or []):
        args += ["--engine", e]
    for t in (targets or []):
        args += ["--target", t]

    start = time.time()
    log.info("db_archive_ops.create: %s", " ".join(args))
    r = subprocess.run(
        args, capture_output=True, text=True,
        timeout=Config.SNAPCTL_TIMEOUT, check=False,
    )
    dur = int(time.time() - start)
    if r.returncode != 0:
        msg = (r.stderr.strip() or r.stdout.strip())[:500]
        raise DbArchiveOpError(
            f"snapctl db-archive rc={r.returncode} (dur={dur}s): {msg or 'sin output'}"
        )
    # Parsea el resumen final del log para sacar "X ok, Y fail"
    ok_count, fail_count = 0, 0
    for line in (r.stderr + r.stdout).splitlines():
        m = re.search(r"DB archive: (\d+) ok, (\d+) fail", line)
        if m:
            ok_count, fail_count = int(m.group(1)), int(m.group(2))
            break
    return {
        "duration_s": dur,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "engines": engines or [],
        "targets": targets or [],
    }


# ---------------- check connection ----------------

def check_connection(engine: str,
                     password: str | None = None) -> dict:
    """Llama snapctl db-archive-check <engine> y devuelve dict normalizado.

    Si `password` se pasa, snapctl lo usa SOLO para esta llamada (no toca
    local.conf). Útil para validar nuevas credenciales antes de guardar.
    """
    if not _ENGINE_RE.match(engine or ""):
        raise DbArchiveOpError(f"engine no válido: {engine}")

    bin_path = str(Config.SNAPCTL_BIN)
    args = [bin_path, "db-archive-check", engine]
    if password is not None:
        args += ["--password", password]
    try:
        r = subprocess.run(
            args, capture_output=True, text=True,
            timeout=15, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "engine": engine, "error": "timeout >15s"}
    raw = (r.stdout or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "engine": engine,
            "error": (raw or r.stderr.strip() or f"rc={r.returncode}")[:300],
        }
