"""Conector entre el shared Drive y el caché en DB del inventario.

Antes este módulo construía el árbol de auditoría llamando a `rclone
lsjson -R` en cada request y guardando un caché de 30s en memoria.
Ahora la fuente de verdad sigue siendo Drive, pero los datos se
materializan en `drive_inventory` (vía `backend.central.inventory`) y la
UI lee siempre de DB. Solo el botón "Refrescar" (o `?force=1`) gatilla
un scan rclone que reescribe esas tablas atómicamente.
"""
from __future__ import annotations

import json
import logging
import subprocess

from ..central import inventory as inv

log = logging.getLogger(__name__)


class AuditTreeError(Exception):
    pass


class AuditTreeService:
    def __init__(
        self,
        *,
        conn,
        rclone_bin,
        rclone_config,
        remote: str,
        remote_path: str,
        shrink_pct: int = 20,
    ):
        # `conn` es el DB_CONN compartido (raw sqlite3.Connection en
        # autocommit, mismo que usan heartbeats/alerts). Usar el mismo conn
        # garantiza que las transacciones BEGIN/COMMIT no compitan con
        # lecturas en una conexión paralela.
        self.conn = conn
        self.rclone_bin = rclone_bin
        self.rclone_config = rclone_config
        self.remote = remote.rstrip(":")
        self.remote_path = remote_path.strip("/")
        self.shrink_pct = max(1, min(99, int(shrink_pct or 20)))

    # Compat: antes era el reset del caché en memoria. Hoy la DB ES el
    # caché persistente, así que "invalidar" no aplica — el scan solo se
    # gatilla por el botón Refrescar (refresh()) o por ?force=1. Dejamos
    # esto como NO-OP para que callers existentes (p. ej. después de
    # cambiar AUDIT_REMOTE_PATH desde /api/central/config) no disparen
    # scans no solicitados.
    def invalidate_cache(self) -> None:
        return None

    def _rclone(self, *args: str, timeout: int = 120) -> str:
        if not self.rclone_bin.exists():
            raise AuditTreeError(f"rclone no encontrado en {self.rclone_bin}")
        if not self.rclone_config.exists():
            raise AuditTreeError(
                f"rclone.conf no existe en {self.rclone_config}. Vincula Drive primero."
            )
        cmd = [str(self.rclone_bin), "--config", str(self.rclone_config), *args]
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            raise AuditTreeError(f"rclone timeout ({timeout}s)")
        if r.returncode != 0:
            err = r.stderr.strip()[:300]
            if "directory not found" in err.lower() or "not found" in err.lower():
                return "[]"
            raise AuditTreeError(f"rclone rc={r.returncode}: {err}")
        return r.stdout

    def _list_all(self) -> list[dict]:
        # Si AUDIT_REMOTE_PATH está vacío o "/", escaneamos desde la raíz
        # del remote (gdrive:proyecto/entorno/pais/...). Si tiene valor
        # (p. ej. "snapshots"), escaneamos solo esa subcarpeta. La
        # taxonomía esperada bajo este punto siempre es:
        #   <proyecto>/<entorno>/<pais>/<os|db>/<subkey>/<label>/Y/M/D/<file>
        # rclone lsjson devuelve paths relativos al directorio que pidamos,
        # así que el parser sigue contando 10 segmentos en cualquier caso.
        path = f"{self.remote}:"
        if self.remote_path:
            path += self.remote_path
        raw = self._rclone(
            "lsjson", "-R", "--files-only", "--no-modtime", "--fast-list", path
        )
        try:
            return json.loads(raw or "[]")
        except json.JSONDecodeError as e:
            raise AuditTreeError(f"lsjson devolvió JSON inválido: {e}")

    def refresh(self, *, triggered_by: str = "manual") -> dict:
        """Scan completo de Drive → reescribe drive_inventory atómicamente.

        rclone corre primero (subproceso, segundos). El bulk-insert sobre
        DB es luego, en una única transacción.
        """
        items = self._list_all()
        return inv.apply_drive_scan(
            self.conn, items,
            shrink_pct=self.shrink_pct,
            triggered_by=triggered_by,
        )

    def build_tree(self, force: bool = False) -> dict:
        """Devuelve el árbol agregado. force=True hace scan antes de leer."""
        if force:
            self.refresh(triggered_by="manual")
        return inv.read_tree(self.conn, shrink_pct=self.shrink_pct)

    def build_local_view(self, *, proyecto: str, entorno: str, pais: str,
                         label: str, force: bool = False) -> dict:
        """Vista de un único cliente. force=True scaneará TODO el Drive
        (no solo el subárbol del cliente) — es el botón refrescar global."""
        if force:
            self.refresh(triggered_by="manual")
        return inv.read_local_view(
            self.conn,
            proyecto=proyecto, entorno=entorno, pais=pais, label=label,
            shrink_pct=self.shrink_pct,
        )

    def last_scan(self) -> dict | None:
        return inv.last_scan(self.conn)
