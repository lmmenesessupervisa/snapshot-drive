"""Servicio de auditoría agregada — lee el shared Drive y expone un resumen
por cliente para la vista /audit.

Fuente de verdad: cada host escribe /{AUDIT_REMOTE_PATH}/_status/<host>.json
al terminar cada operación (ver core/lib/common.sh:write_status_drive).
Aquí listamos ese directorio con `rclone lsjson`, descargamos cada JSON con
`rclone cat` y agregamos en memoria.

Como un `gdrive lsjson` puede tardar 1-3s por la API de Drive, metemos un
cache TTL corto (10s por defecto) para que la UI pueda auto-refrescar sin
castigar a Google. El cache vive en proceso — asumimos gunicorn con 1 worker
(ver systemd/snapshot-backend.service).
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class AuditError(Exception):
    pass


@dataclass
class CachedValue:
    ts: float
    value: Any


@dataclass
class ClientStatus:
    host: str
    last: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    updated_ts: str = ""
    # Derivados — calculados a partir de los campos anteriores.
    silent_hours: float | None = None   # horas desde último backup OK
    health: str = "unknown"             # ok | fail | silent | unknown | running


class AuditService:
    def __init__(
        self,
        rclone_bin: Path,
        rclone_config: Path,
        remote: str,
        remote_path: str,
        cache_ttl_s: int = 10,
        silence_threshold_h: float = 36.0,
    ):
        self.rclone_bin = Path(rclone_bin)
        self.rclone_config = Path(rclone_config)
        self.remote = remote.rstrip(":")
        self.remote_path = remote_path.strip("/")
        self.cache_ttl = cache_ttl_s
        self.silence_threshold_h = silence_threshold_h
        self._cache: dict[str, CachedValue] = {}

    # ---------------- plumbing ----------------
    def _rclone(self, *args: str, timeout: int = 30) -> str:
        if not self.rclone_bin.exists():
            raise AuditError(f"rclone no encontrado en {self.rclone_bin}")
        if not self.rclone_config.exists():
            raise AuditError(
                f"rclone.conf no existe en {self.rclone_config}. Vincula Drive primero."
            )
        cmd = [str(self.rclone_bin), "--config", str(self.rclone_config), *args]
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            raise AuditError(f"rclone timeout ({timeout}s): {' '.join(args)}")
        if res.returncode != 0:
            raise AuditError(
                f"rclone {args[0] if args else ''} rc={res.returncode}: "
                f"{res.stderr.strip()[:300]}"
            )
        return res.stdout

    def _cache_get(self, key: str) -> Any | None:
        item = self._cache.get(key)
        if item and (time.time() - item.ts) < self.cache_ttl:
            return item.value
        return None

    def _cache_set(self, key: str, value: Any) -> None:
        self._cache[key] = CachedValue(ts=time.time(), value=value)

    def invalidate_cache(self) -> None:
        self._cache.clear()

    # ---------------- reading Drive ----------------
    def _list_status_files(self) -> list[dict]:
        """Devuelve la lista raw de ficheros <host>.json en _status/."""
        path = f"{self.remote}:{self.remote_path}/_status/"
        try:
            out = self._rclone("lsjson", path, "--no-modtime", "--fast-list", timeout=30)
        except AuditError as e:
            # Si el directorio no existe aún, rclone devuelve error. Tratamos como lista vacía.
            if "directory not found" in str(e).lower() or "not found" in str(e).lower():
                return []
            raise
        try:
            items = json.loads(out or "[]")
        except json.JSONDecodeError as e:
            raise AuditError(f"rclone lsjson devolvió JSON inválido: {e}")
        return [it for it in items if it.get("Name", "").endswith(".json") and not it.get("IsDir")]

    def _read_status(self, host: str) -> dict:
        path = f"{self.remote}:{self.remote_path}/_status/{host}.json"
        raw = self._rclone("cat", path, timeout=20)
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}

    # ---------------- public API ----------------
    def get_all(self, force_refresh: bool = False) -> list[ClientStatus]:
        if not force_refresh:
            cached = self._cache_get("all")
            if cached is not None:
                return cached

        try:
            files = self._list_status_files()
        except AuditError as e:
            log.warning("audit lsjson falló: %s", e)
            raise

        clients: list[ClientStatus] = []
        now = time.time()
        for f in files:
            name = f.get("Name", "")
            host = name[:-5] if name.endswith(".json") else name
            try:
                raw = self._read_status(host)
            except AuditError as e:
                log.warning("audit cat %s falló: %s", host, e)
                clients.append(ClientStatus(host=host, health="unknown"))
                continue
            c = ClientStatus(
                host=raw.get("host") or host,
                last=raw.get("last") or {},
                totals=raw.get("totals") or {},
                history=raw.get("history") or [],
                updated_ts=raw.get("updated_ts") or "",
            )
            c.health, c.silent_hours = self._classify(c, now)
            clients.append(c)

        clients.sort(key=lambda c: c.host.lower())
        self._cache_set("all", clients)
        return clients

    def _classify(self, c: ClientStatus, now_ts: float) -> tuple[str, float | None]:
        """Clasifica el cliente en ok/fail/silent/running/unknown."""
        last = c.last or {}
        last_status = last.get("status", "")
        last_ts_str = c.totals.get("last_successful_backup_ts") or last.get("ts", "")

        if last_status == "running":
            return "running", None

        silent_h: float | None = None
        if last_ts_str:
            try:
                from datetime import datetime
                last_dt = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
                silent_h = (now_ts - last_dt.timestamp()) / 3600
            except (ValueError, TypeError):
                silent_h = None

        if last_status == "fail":
            return "fail", silent_h
        if last_status == "ok":
            if silent_h is not None and silent_h > self.silence_threshold_h:
                return "silent", silent_h
            return "ok", silent_h
        return "unknown", silent_h


def client_to_dict(c: ClientStatus) -> dict:
    return {
        "host": c.host,
        "last": c.last,
        "totals": c.totals,
        "history": c.history[:20],   # drill-down muestra los 20 más recientes
        "updated_ts": c.updated_ts,
        "health": c.health,
        "silent_hours": round(c.silent_hours, 2) if c.silent_hours is not None else None,
    }
