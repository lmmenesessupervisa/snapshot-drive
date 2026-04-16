"""Wrapper tipado alrededor del CLI `snapctl`.

Regla de negocio: esta capa NO duplica lógica del core — sólo traduce
llamadas HTTP en llamadas al binario `snapctl` y persiste el resultado.
"""
import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..models.db import DB


class _TTLCache:
    """Cache trivial con TTL por clave. Thread-safe."""
    def __init__(self):
        self._store: dict[str, tuple[object, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float):
        with self._lock:
            entry = self._store.get(key)
            if entry and (time.time() - entry[1]) < ttl:
                return entry[0]
            return None

    def set(self, key: str, value):
        with self._lock:
            self._store[key] = (value, time.time())

    def invalidate(self, *keys: str):
        with self._lock:
            for k in keys:
                self._store.pop(k, None)

    def invalidate_all(self):
        with self._lock:
            self._store.clear()

log = logging.getLogger("snapctl")

# Validación estricta de IDs de snapshot restic (hex 8–64)
_ID_RE = re.compile(r"^[a-f0-9]{8,64}$")
# Validación de tag (alfa-num-_-. max 64)
_TAG_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
# Paths de restore: absolutos, sin '..'
def _valid_path(p: str) -> bool:
    if not p or ".." in p.split("/"):
        return False
    return p.startswith("/")


class SnapctlError(Exception):
    def __init__(self, message: str, rc: int = 1, stderr: str = ""):
        super().__init__(message)
        self.rc = rc
        self.stderr = stderr


@dataclass
class Result:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


class SnapctlService:
    # TTLs por clave de cache
    TTL_STATUS_FAST = 25     # dashboard auto-refresh cada 30s → hits
    TTL_STATUS_DEEP = 120    # conteo de Drive (caro) — válido 2 min
    TTL_LIST        = 90     # snapshots (local+drive) — ~15s consultar

    def __init__(self, db: DB, bin_path: Path | None = None, timeout: int = 3600):
        self.bin = Path(bin_path or Config.SNAPCTL_BIN)
        self.timeout = timeout
        self.db = db
        self.cache = _TTLCache()
        if not self.bin.exists():
            log.warning("snapctl no encontrado en %s", self.bin)

    # ---------- núcleo ----------
    def _run(self, args: list[str], kind: str, snapshot_id: str | None = None) -> Result:
        cmd = [str(self.bin), *args]
        log.info("exec: %s", shlex.join(cmd))
        job_id = self.db.job_start(kind, json.dumps(args), snapshot_id)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            res = Result(proc.returncode, proc.stdout or "", proc.stderr or "")
        except subprocess.TimeoutExpired as e:
            res = Result(124, "", f"timeout tras {self.timeout}s: {e}")
        except FileNotFoundError as e:
            res = Result(127, "", f"binario no encontrado: {e}")
        finally:
            pass
        self.db.job_finish(job_id, res.rc, res.stdout, res.stderr)
        return res

    # ---------- operaciones ----------
    def list_snapshots(self, fresh: bool = False) -> list[dict]:
        if not fresh:
            cached = self.cache.get("list", self.TTL_LIST)
            if cached is not None:
                return cached
        r = self._run(["list", "--json"], "list")
        if not r.ok:
            raise SnapctlError("no se pudo listar snapshots", r.rc, r.stderr)
        try:
            data = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            data = []
        self.cache.set("list", data)
        return data

    def create(self, tag: str = "manual") -> dict:
        if not _TAG_RE.match(tag):
            raise SnapctlError("tag inválido", 2, tag)
        r = self._run(["create", "--tag", tag], "create")
        if not r.ok:
            raise SnapctlError("fallo al crear snapshot", r.rc, r.stderr)
        self.cache.invalidate_all()
        return {"stdout": r.stdout[-4000:], "rc": r.rc}

    def estimate(self, fresh: bool = False) -> dict:
        # Resultado válido 5 min — rescanear es caro (segundos-minutos).
        if not fresh:
            cached = self.cache.get("estimate", 300)
            if cached is not None:
                return cached
        r = self._run(["estimate", "--json"], "estimate")
        if not r.ok:
            raise SnapctlError("no se pudo estimar peso del snapshot",
                               r.rc, r.stderr)
        try:
            data = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            data = {"raw": r.stdout}
        self.cache.set("estimate", data)
        return data

    def delete(self, sid: str) -> dict:
        if not _ID_RE.match(sid):
            raise SnapctlError("id inválido", 2, sid)
        r = self._run(["delete", sid], "delete", sid)
        if not r.ok:
            raise SnapctlError("fallo al eliminar", r.rc, r.stderr)
        self.cache.invalidate_all()
        return {"stdout": r.stdout[-2000:], "rc": r.rc}

    def restore(self, sid: str, target: str, include: str | None = None) -> dict:
        if not _ID_RE.match(sid):
            raise SnapctlError("id inválido", 2, sid)
        if not _valid_path(target):
            raise SnapctlError("target inválido", 2, target)
        args = ["restore", sid, "--target", target]
        if include:
            if not _valid_path(include):
                raise SnapctlError("include inválido", 2, include)
            args += ["--include", include]
        r = self._run(args, "restore", sid)
        if not r.ok:
            raise SnapctlError("fallo al restaurar", r.rc, r.stderr)
        return {"stdout": r.stdout[-4000:], "rc": r.rc}

    def prune(self) -> dict:
        r = self._run(["prune"], "prune")
        if not r.ok:
            raise SnapctlError("fallo al aplicar retención", r.rc, r.stderr)
        self.cache.invalidate_all()
        return {"stdout": r.stdout[-4000:], "rc": r.rc}

    def cleanup_local(self) -> dict:
        r = self._run(["cleanup-local"], "cleanup-local")
        if not r.ok:
            raise SnapctlError("fallo al limpiar buffer local", r.rc, r.stderr)
        self.cache.invalidate_all()
        return {"stdout": r.stdout[-4000:], "rc": r.rc}

    def check(self) -> dict:
        r = self._run(["check"], "check")
        if not r.ok:
            raise SnapctlError("check falló", r.rc, r.stderr)
        return {"stdout": r.stdout[-4000:], "rc": r.rc}

    def unlock(self) -> dict:
        r = self._run(["unlock"], "unlock")
        if not r.ok:
            raise SnapctlError("unlock falló", r.rc, r.stderr)
        return {"stdout": r.stdout[-2000:], "rc": r.rc}

    def sync(self) -> dict:
        r = self._run(["sync"], "sync")
        if not r.ok:
            raise SnapctlError("sync falló", r.rc, r.stderr)
        return {"stdout": r.stdout[-4000:], "rc": r.rc}

    def reconcile(self) -> dict:
        r = self._run(["reconcile"], "reconcile")
        if not r.ok:
            raise SnapctlError("reconcile falló", r.rc, r.stderr)
        self.cache.invalidate_all()
        return {"stdout": r.stdout[-4000:], "rc": r.rc}

    def status(self, fast: bool = True, fresh: bool = False) -> dict:
        """Estado del sistema.

        fast=True: no consulta restic sobre Drive (auto-refresh del dashboard).
        fast=False: conteo completo — se invoca cuando el usuario lo pide.
        """
        key = "status_fast" if fast else "status_deep"
        ttl = self.TTL_STATUS_FAST if fast else self.TTL_STATUS_DEEP
        if not fresh:
            cached = self.cache.get(key, ttl)
            if cached is not None:
                return cached
        args = ["status", "--json"] + (["--fast"] if fast else [])
        r = self._run(args, "status")
        if not r.ok:
            raise SnapctlError("status falló", r.rc, r.stderr)
        try:
            data = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            data = {"raw": r.stdout}
        self.cache.set(key, data)
        return data

    # ---------- Drive / rclone ----------
    def drive_status(self) -> dict:
        r = self._run(["drive-status"], "drive-status")
        if not r.ok:
            raise SnapctlError("drive-status falló", r.rc, r.stderr)
        try:
            return json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            return {"raw": r.stdout}

    def drive_link(self, token_json: str, team_drive: str = "") -> dict:
        # Validación del token aquí, antes de pasar al CLI
        try:
            d = json.loads(token_json)
            assert isinstance(d, dict) and "access_token" in d and "refresh_token" in d
        except Exception as e:
            raise SnapctlError(f"token inválido: {e}", 2)
        if team_drive and not re.match(r"^[A-Za-z0-9_\-]{1,64}$", team_drive):
            raise SnapctlError("team_drive id inválido", 2)

        # Escribir token a fichero temporal seguro y pasarlo al CLI
        with tempfile.NamedTemporaryFile("w", delete=False, prefix="rclone-tok-", dir="/tmp") as tf:
            os.chmod(tf.name, 0o600)
            tf.write(token_json)
            tf.flush()
            tok_path = tf.name
        try:
            args = ["drive-link", tok_path]
            if team_drive:
                args.append(team_drive)
            r = self._run(args, "drive-link")
        finally:
            try: os.unlink(tok_path)
            except OSError: pass
        if not r.ok:
            raise SnapctlError("vinculación falló", r.rc, r.stderr)
        return {"ok": True}

    def drive_unlink(self) -> dict:
        r = self._run(["drive-unlink"], "drive-unlink")
        if not r.ok:
            raise SnapctlError("unlink falló", r.rc, r.stderr)
        return {"ok": True}

    def drive_shared_list(self) -> list[dict]:
        r = self._run(["drive-shared-list"], "drive-shared-list")
        if not r.ok:
            raise SnapctlError("no se pudo listar unidades compartidas", r.rc, r.stderr)
        try:
            return json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            return []

    def drive_set_target(self, kind: str, shared_id: str | None = None) -> dict:
        if kind not in ("personal", "shared"):
            raise SnapctlError("tipo inválido", 2)
        args = ["drive-target", kind]
        if kind == "shared":
            if not shared_id or not re.match(r"^[A-Za-z0-9_\-]{1,64}$", shared_id):
                raise SnapctlError("id de unidad compartida inválido", 2)
            args.append(shared_id)
        r = self._run(args, "drive-target")
        if not r.ok:
            raise SnapctlError("no se pudo aplicar target", r.rc, r.stderr)
        return {"ok": True}

    def logs(self, lines: int = 200) -> str:
        lines = max(1, min(int(lines), 5000))
        r = self._run(["logs", "--lines", str(lines)], "logs")
        return r.stdout
