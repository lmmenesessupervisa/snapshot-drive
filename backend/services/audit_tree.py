"""Construye el árbol agregado de backups en el shared Drive.

Hace UN solo `rclone lsjson -R --files-only` sobre la raíz configurada y
agrupa los archivos por la taxonomía:

    <root>/<proyecto>/<entorno>/<pais>/<category>/<subkey>/<label>/YYYY/MM/DD/<file>

donde:
  - category ∈ {os, db}
  - subkey   ∈ {linux, postgres, mysql, mongo}
  - label    = hostname (os) o dbname (db)

Devuelve estructura jerárquica con counts/totales por nivel + lista de
los 5 archivos más recientes por leaf.

Cache TTL 30s — un lsjson recursivo sobre Drive con muchos archivos
puede tardar 5-15s; queremos que las navegaciones sean baratas.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


class AuditTreeError(Exception):
    pass


@dataclass
class _Cached:
    ts: float
    value: Any


_FNAME_RE = re.compile(
    r"^servidor_(?P<label>[A-Za-z0-9_.\-]+)_(?P<ts>\d{8}_\d{6})\."
    r"(?P<ext>(?:tar|sql|archive)\.zst(?:\.enc|\.age)?)$"
)


def _parse_fname(name: str) -> dict | None:
    m = _FNAME_RE.match(name)
    if not m:
        return None
    ext = m.group("ext")
    return {
        "label": m.group("label"),
        "ts": m.group("ts"),
        "ext": ext,
        "encrypted": ext.endswith(".enc") or ext.endswith(".age"),
        "crypto": "age" if ext.endswith(".age") else ("openssl" if ext.endswith(".enc") else "none"),
    }


def _ts_to_iso(ts: str) -> str:
    """20260428_063012 → 2026-04-28T06:30:12Z"""
    if len(ts) != 15:
        return ""
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:{ts[13:15]}Z"


class AuditTreeService:
    def __init__(
        self,
        rclone_bin,
        rclone_config,
        remote: str,
        remote_path: str,
        cache_ttl_s: int = 30,
    ):
        self.rclone_bin = rclone_bin
        self.rclone_config = rclone_config
        self.remote = remote.rstrip(":")
        self.remote_path = remote_path.strip("/")
        self.cache_ttl = cache_ttl_s
        self._cache: dict[str, _Cached] = {}

    def invalidate_cache(self) -> None:
        self._cache.clear()

    def _rclone(self, *args: str, timeout: int = 60) -> str:
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
        path = f"{self.remote}:{self.remote_path}/"
        raw = self._rclone(
            "lsjson", "-R", "--files-only", "--no-modtime", "--fast-list", path
        )
        try:
            return json.loads(raw or "[]")
        except json.JSONDecodeError as e:
            raise AuditTreeError(f"lsjson devolvió JSON inválido: {e}")

    def build_tree(self, force: bool = False) -> dict:
        """Devuelve {summary, proyectos: [...]} con la jerarquía completa."""
        if not force:
            hit = self._cache.get("tree")
            if hit and (time.time() - hit.ts) < self.cache_ttl:
                return hit.value

        items = self._list_all()
        # Filas indexadas por path: cada cliente es un (proyecto, entorno, pais, label).
        # category/subkey distingue mensual (os/linux) vs DB (db/<engine>).
        # leaves[(proyecto, entorno, pais, label, category, subkey)] = list[file dict]
        leaves: dict[tuple, list[dict]] = defaultdict(list)

        for it in items:
            if it.get("IsDir"):
                continue
            full_path = it.get("Path") or it.get("Name", "")
            parts = full_path.split("/")
            # Esperamos exactamente 8 segmentos:
            #   proyecto, entorno, pais, category, subkey, label, year, month, day, fname
            # Total = 10. Algunos clientes viejos no usan year/month/day → toleramos 7 segs:
            if len(parts) < 7:
                continue
            proyecto, entorno, pais, category, subkey, label = parts[0:6]
            fname = parts[-1]
            if category not in ("os", "db"):
                continue
            parsed = _parse_fname(fname)
            if not parsed:
                continue
            file_iso = _ts_to_iso(parsed["ts"])
            leaves[(proyecto, entorno, pais, label, category, subkey)].append({
                "name": fname,
                "path": full_path,
                "size": int(it.get("Size", 0) or 0),
                "ts": parsed["ts"],
                "ts_iso": file_iso,
                "modified": it.get("ModTime") or file_iso,
                "encrypted": parsed["encrypted"],
                "crypto": parsed["crypto"],
            })

        # Aggregate per proyecto > (entorno, pais) > cliente > backup type
        tree: dict[str, dict] = {}
        all_clients: set[tuple] = set()
        all_files = 0
        all_size = 0
        latest_ts_iso = ""

        for key, files in leaves.items():
            proyecto, entorno, pais, label, category, subkey = key
            files.sort(key=lambda f: f["ts"], reverse=True)
            count = len(files)
            size = sum(f["size"] for f in files)
            newest = files[0]
            oldest = files[-1]
            enc_count = sum(1 for f in files if f["encrypted"])

            # Update global rollups
            all_files += count
            all_size += size
            all_clients.add((proyecto, entorno, pais, label))
            if newest["ts_iso"] > latest_ts_iso:
                latest_ts_iso = newest["ts_iso"]

            p = tree.setdefault(proyecto, {
                "name": proyecto,
                "files": 0, "size": 0, "clients": 0,
                "last_ts": "",
                "regions": {},
            })
            p["files"] += count
            p["size"] += size
            if newest["ts_iso"] > p["last_ts"]:
                p["last_ts"] = newest["ts_iso"]

            region_key = f"{entorno}/{pais}"
            r = p["regions"].setdefault(region_key, {
                "entorno": entorno, "pais": pais,
                "files": 0, "size": 0, "clients": {},
            })
            r["files"] += count
            r["size"] += size

            cli = r["clients"].setdefault(label, {
                "label": label,
                "files": 0, "size": 0,
                "last_ts": "",
                "monthly": None,        # dict si tiene backup os/linux
                "db": [],               # lista de dicts (uno por engine:dbname)
            })
            cli["files"] += count
            cli["size"] += size
            if newest["ts_iso"] > cli["last_ts"]:
                cli["last_ts"] = newest["ts_iso"]

            entry = {
                "category": category,
                "subkey": subkey,
                "engine": subkey,                # alias para DB
                "count": count,
                "size": size,
                "encrypted_count": enc_count,
                "newest_ts": newest["ts_iso"],
                "newest_path": newest["path"],
                "newest_crypto": newest["crypto"],
                "oldest_ts": oldest["ts_iso"],
                "recent": files[:5],             # 5 más recientes para drilldown
            }
            if category == "os" and subkey == "linux":
                cli["monthly"] = entry
            else:
                cli["db"].append(entry)

        # Convertir dicts internos a listas ordenadas
        proyectos_out = []
        for pname in sorted(tree.keys()):
            p = tree[pname]
            regions_out = []
            cli_count_p = 0
            for rk in sorted(p["regions"].keys()):
                r = p["regions"][rk]
                clients_list = []
                for clbl in sorted(r["clients"].keys()):
                    c = r["clients"][clbl]
                    c["db"].sort(key=lambda d: (d["subkey"], d["engine"]))
                    clients_list.append(c)
                regions_out.append({
                    "entorno": r["entorno"],
                    "pais": r["pais"],
                    "files": r["files"],
                    "size": r["size"],
                    "clients": clients_list,
                })
                cli_count_p += len(clients_list)
            p["clients"] = cli_count_p
            del p["regions"]
            proyectos_out.append({**p, "regions": regions_out})

        result = {
            "summary": {
                "proyectos": len(proyectos_out),
                "clients": len(all_clients),
                "files": all_files,
                "size_bytes": all_size,
                "last_backup_ts": latest_ts_iso or None,
                "scanned_at": int(time.time()),
            },
            "proyectos": proyectos_out,
        }
        self._cache["tree"] = _Cached(ts=time.time(), value=result)
        return result
