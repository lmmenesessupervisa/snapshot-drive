"""Caché en DB del inventario del shared Drive.

Drive es la fuente de verdad. Estas funciones lo materializan en
`drive_inventory` + `drive_inventory_files` para que la auditoría sea
sub-segundo en cada cambio de vista. El botón "Refrescar" gatilla
`apply_drive_scan`, y los heartbeats con campo `inventory` actualizan el
subárbol de su propio cliente vía `apply_client_inventory`.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable


# Anclamos en el timestamp YYYYMMDD_HHMMSS (15 chars), que es invariante
# entre los distintos prefijos del proyecto:
#   servidor_<host>_<ts>.tar.zst[.enc|.age]   (modelo legacy / OS)
#   postgresql_<dbname>_<ts>.7z               (DB postgres)
#   mysql_<dbname>_<ts>.7z                    (DB mysql)
#   mongo_<dbname>_<ts>.7z                    (DB mongo)
#   ...
# El label de UI se toma del PATH (parts[5]), no del filename, por lo que
# nombres con underscore como `clientes_db` no confunden al parser.
_FNAME_RE = re.compile(
    r"^.+?_(?P<ts>\d{8}_\d{6})(?:\.(?P<ext>[A-Za-z0-9.]+))?$"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_fname(name: str) -> dict | None:
    """Extrae timestamp + cifrado del filename. None si no matchea."""
    m = _FNAME_RE.match(name)
    if not m:
        return None
    ext = (m.group("ext") or "").lower()
    # Detección de cifrado por sufijo del filename:
    # .age      → age (encrypted)
    # .enc      → openssl (encrypted)
    # cualquier otra (incluido .7z, .tar.zst, .sql) → no detectable.
    # Nota: 7z puede llevar password pero no es detectable desde el nombre.
    if ext.endswith(".age"):
        encrypted, crypto = True, "age"
    elif ext.endswith(".enc"):
        encrypted, crypto = True, "openssl"
    else:
        encrypted, crypto = False, "none"
    return {
        "ts": m.group("ts"),
        "ext": ext,
        "encrypted": encrypted,
        "crypto": crypto,
    }


def ts_to_iso(ts: str) -> str:
    """20260428_063012 → 2026-04-28T06:30:12Z."""
    if len(ts) != 15:
        return ""
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:{ts[13:15]}Z"


def _file_from_lsjson(item: dict, fname: str, parsed: dict, full_path: str) -> dict:
    file_iso = ts_to_iso(parsed["ts"])
    return {
        "name": fname,
        "path": full_path,
        "size": int(item.get("Size", 0) or 0),
        "ts": parsed["ts"],
        "ts_iso": file_iso,
        "modified": item.get("ModTime") or file_iso,
        "encrypted": parsed["encrypted"],
        "crypto": parsed["crypto"],
    }


def lsjson_to_leaves(items: Iterable[dict]) -> dict:
    """rclone lsjson -R items → {(p,e,pa,label,cat,sub) → [file dict]}.

    Taxonomía: proyecto/entorno/pais/category/subkey/label/.../<file>.
    category ∈ {os, db}; subkey libre (linux|postgres|mysql|mongo|...).
    """
    leaves: dict[tuple, list[dict]] = defaultdict(list)
    for it in items:
        if it.get("IsDir"):
            continue
        full_path = it.get("Path") or it.get("Name", "")
        parts = full_path.split("/")
        if len(parts) < 7:
            continue
        proyecto, entorno, pais, category, subkey, label = parts[0:6]
        if category not in ("os", "db"):
            continue
        fname = parts[-1]
        parsed = parse_fname(fname)
        if not parsed:
            continue
        leaves[(proyecto, entorno, pais, label, category, subkey)].append(
            _file_from_lsjson(it, fname, parsed, full_path)
        )
    return leaves


def compute_leaf_aggregate(files: list[dict], *, shrink_pct: int) -> dict:
    """Agrega los files de un leaf. Detecta shrink (último vs anterior)."""
    files = sorted(files, key=lambda f: f["ts"], reverse=True)
    count = len(files)
    size = sum(f["size"] for f in files)
    enc = sum(1 for f in files if f["encrypted"])
    newest = files[0]
    oldest = files[-1]
    prev = files[1] if len(files) >= 2 else None
    shrunk = False
    delta_pct: float | None = None
    prev_size = prev["size"] if prev else None
    if prev and prev["size"] > 0 and newest["size"] >= 0:
        delta_pct = round(100 * (prev["size"] - newest["size"]) / prev["size"], 1)
        if delta_pct >= shrink_pct:
            shrunk = True
    return {
        "count": count,
        "size": size,
        "encrypted_count": enc,
        "newest_ts": newest["ts_iso"],
        "newest_path": newest["path"],
        "newest_crypto": newest["crypto"],
        "newest_size": newest["size"],
        "prev_size": prev_size,
        "shrunk": shrunk,
        "shrink_delta_pct": delta_pct,
        "oldest_ts": oldest["ts_iso"],
        "files_sorted_desc": files,
    }


# ----------------------- writes -----------------------

# Cuántos archivos guardamos por leaf en drive_inventory_files. La vista
# del árbol muestra 5; la vista local muestra TODOS — pero almacenar
# > unas decenas por leaf rara vez aporta valor y crece con cada scan.
_INVENTORY_FILES_CAP = 50


def _upsert_leaf(conn, *, proyecto, entorno, pais, label, category, subkey,
                 agg, source, now):
    cur = conn.execute(
        "SELECT id FROM drive_inventory WHERE proyecto=? AND entorno=? "
        "AND pais=? AND label=? AND category=? AND subkey=?",
        (proyecto, entorno, pais, label, category, subkey),
    )
    row = cur.fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO drive_inventory "
            "(proyecto, entorno, pais, label, category, subkey, "
            " count_files, total_size_bytes, encrypted_count, "
            " newest_ts, newest_path, newest_crypto, newest_size, "
            " prev_size, oldest_ts, shrunk, shrink_delta_pct, "
            " source, last_updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (proyecto, entorno, pais, label, category, subkey,
             agg["count"], agg["size"], agg["encrypted_count"],
             agg["newest_ts"], agg["newest_path"], agg["newest_crypto"],
             agg["newest_size"], agg["prev_size"], agg["oldest_ts"],
             1 if agg["shrunk"] else 0, agg["shrink_delta_pct"],
             source, now),
        )
        leaf_id = cur.lastrowid
    else:
        leaf_id = row[0]
        conn.execute(
            "UPDATE drive_inventory SET "
            " count_files=?, total_size_bytes=?, encrypted_count=?, "
            " newest_ts=?, newest_path=?, newest_crypto=?, newest_size=?, "
            " prev_size=?, oldest_ts=?, shrunk=?, shrink_delta_pct=?, "
            " source=?, last_updated_at=? "
            "WHERE id=?",
            (agg["count"], agg["size"], agg["encrypted_count"],
             agg["newest_ts"], agg["newest_path"], agg["newest_crypto"],
             agg["newest_size"], agg["prev_size"], agg["oldest_ts"],
             1 if agg["shrunk"] else 0, agg["shrink_delta_pct"],
             source, now, leaf_id),
        )
        conn.execute("DELETE FROM drive_inventory_files WHERE leaf_id=?", (leaf_id,))

    for f in agg["files_sorted_desc"][:_INVENTORY_FILES_CAP]:
        conn.execute(
            "INSERT INTO drive_inventory_files "
            "(leaf_id, name, path, size, ts, ts_iso, modified, encrypted, crypto) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (leaf_id, f["name"], f["path"], f["size"], f["ts"], f["ts_iso"],
             f.get("modified"), 1 if f["encrypted"] else 0, f["crypto"]),
        )
    return leaf_id


def apply_drive_scan(conn, items: list[dict], *, shrink_pct: int,
                     triggered_by: str = "manual") -> dict:
    """Reemplaza todo el inventario con el resultado de un scan completo.

    Crea una fila en drive_scans, hace BEGIN, borra drive_inventory (las
    files caen por CASCADE), reinserta. Si algo falla, marca el scan como
    error y re-raises.
    """
    started_at = _now_iso()
    t0 = time.time()
    cur = conn.execute(
        "INSERT INTO drive_scans (started_at, status, triggered_by) "
        "VALUES (?, 'running', ?)",
        (started_at, triggered_by),
    )
    scan_id = cur.lastrowid

    try:
        leaves = lsjson_to_leaves(items)
        files_total = 0
        size_total = 0
        now = _now_iso()
        conn.execute("BEGIN")
        conn.execute("DELETE FROM drive_inventory")
        for key, files in leaves.items():
            proyecto, entorno, pais, label, cat, sub = key
            agg = compute_leaf_aggregate(files, shrink_pct=shrink_pct)
            files_total += agg["count"]
            size_total += agg["size"]
            _upsert_leaf(
                conn,
                proyecto=proyecto, entorno=entorno, pais=pais, label=label,
                category=cat, subkey=sub, agg=agg,
                source="drive_scan", now=now,
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.execute(
            "UPDATE drive_scans SET status='error', finished_at=?, error=?, "
            "duration_s=? WHERE id=?",
            (_now_iso(), str(e)[:500], time.time() - t0, scan_id),
        )
        raise

    duration = time.time() - t0
    finished_at = _now_iso()
    conn.execute(
        "UPDATE drive_scans SET status='ok', finished_at=?, files_total=?, "
        "size_bytes_total=?, leaves_total=?, duration_s=? WHERE id=?",
        (finished_at, files_total, size_total, len(leaves), duration, scan_id),
    )
    return {
        "scan_id": scan_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_s": round(duration, 3),
        "files_total": files_total,
        "size_bytes_total": size_total,
        "leaves_total": len(leaves),
    }


def apply_client_inventory(conn, payload: dict, *, shrink_pct: int) -> int:
    """Upsert del subárbol de UN cliente desde un heartbeat con `inventory`.

    Solo agrega/actualiza los leaves que el cliente reportó (no borra los
    que no menciona). Esto evita que un client_push parcial — p. ej. con
    truncación al cap de archivos, o un cliente que apenas configuró os/
    pero no db/ — desinforme la DB. La reconciliación final (incluido
    borrar leaves que ya no existen en Drive) la hace el scan completo
    disparado por "Refrescar".
    """
    inv = payload.get("inventory")
    if not inv or not inv.get("leaves"):
        return 0
    client = payload.get("client") or {}
    proyecto = client.get("proyecto") or ""
    entorno = client.get("entorno") or ""
    pais = client.get("pais") or ""
    label = (
        (payload.get("host_meta") or {}).get("hostname")
        or (payload.get("target") or {}).get("label")
        or ""
    )
    if not (proyecto and entorno and pais and label):
        return 0

    now = _now_iso()
    updated = 0
    conn.execute("BEGIN")
    try:
        for leaf in inv["leaves"]:
            cat = leaf.get("category")
            sub = leaf.get("subkey")
            raw_files = leaf.get("files") or []
            if cat not in ("os", "db") or not sub or not raw_files:
                continue
            norm: list[dict] = []
            for f in raw_files:
                ts = f.get("ts") or ""
                norm.append({
                    "name": f.get("name") or "",
                    "path": f.get("path") or "",
                    "size": int(f.get("size") or 0),
                    "ts": ts,
                    "ts_iso": f.get("ts_iso") or ts_to_iso(ts),
                    "modified": f.get("modified"),
                    "encrypted": bool(f.get("encrypted")),
                    "crypto": f.get("crypto") or "none",
                })
            if not norm:
                continue
            agg = compute_leaf_aggregate(norm, shrink_pct=shrink_pct)
            _upsert_leaf(
                conn,
                proyecto=proyecto, entorno=entorno, pais=pais, label=label,
                category=cat, subkey=sub, agg=agg,
                source="client_push", now=now,
            )
            updated += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return updated


# ----------------------- reads -----------------------

_LEAF_COLS = (
    "id, proyecto, entorno, pais, label, category, subkey, "
    "count_files, total_size_bytes, encrypted_count, "
    "newest_ts, newest_path, newest_crypto, newest_size, "
    "prev_size, oldest_ts, shrunk, shrink_delta_pct, "
    "source, last_updated_at"
)


def _row_to_entry(row, recent: list[dict]) -> dict:
    return {
        "category": row["category"],
        "subkey": row["subkey"],
        "engine": row["subkey"],
        "count": row["count_files"],
        "size": row["total_size_bytes"],
        "encrypted_count": row["encrypted_count"],
        "newest_ts": row["newest_ts"] or "",
        "newest_path": row["newest_path"] or "",
        "newest_crypto": row["newest_crypto"] or "",
        "newest_size": row["newest_size"] or 0,
        "prev_size": row["prev_size"],
        "shrunk": bool(row["shrunk"]),
        "shrink_delta_pct": row["shrink_delta_pct"],
        "oldest_ts": row["oldest_ts"] or "",
        "recent": recent,
    }


def _file_row(r) -> dict:
    return {
        "name": r["name"],
        "path": r["path"],
        "size": r["size"],
        "ts": r["ts"],
        "ts_iso": r["ts_iso"],
        "modified": r["modified"],
        "encrypted": bool(r["encrypted"]),
        "crypto": r["crypto"],
    }


def _files_grouped(conn, leaf_ids: list[int], *, cap: int | None) -> dict:
    if not leaf_ids:
        return {}
    placeholders = ",".join("?" * len(leaf_ids))
    rows = conn.execute(
        f"SELECT leaf_id, name, path, size, ts, ts_iso, modified, encrypted, crypto "
        f"FROM drive_inventory_files WHERE leaf_id IN ({placeholders}) "
        f"ORDER BY leaf_id, ts DESC",
        leaf_ids,
    ).fetchall()
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        lst = out[r["leaf_id"]]
        if cap is None or len(lst) < cap:
            lst.append(_file_row(r))
    return out


def read_tree(conn, *, shrink_pct: int) -> dict:
    """Mismo shape que el viejo AuditTreeService.build_tree, leído de DB."""
    rows = conn.execute(
        f"SELECT {_LEAF_COLS} FROM drive_inventory "
        f"ORDER BY proyecto, entorno, pais, label, category, subkey"
    ).fetchall()
    leaf_ids = [r["id"] for r in rows]
    files_by_leaf = _files_grouped(conn, leaf_ids, cap=5)

    tree: dict[str, dict] = {}
    all_clients: set[tuple] = set()
    all_files = 0
    all_size = 0
    latest_ts_iso = ""
    shrunk_total = 0

    for r in rows:
        entry = _row_to_entry(r, files_by_leaf.get(r["id"], []))
        proyecto, entorno = r["proyecto"], r["entorno"]
        pais, label = r["pais"], r["label"]
        count, size = entry["count"], entry["size"]
        shrunk = entry["shrunk"]
        newest_ts = entry["newest_ts"]
        if shrunk:
            shrunk_total += 1
        all_files += count
        all_size += size
        all_clients.add((proyecto, entorno, pais, label))
        if newest_ts > latest_ts_iso:
            latest_ts_iso = newest_ts

        p = tree.setdefault(proyecto, {
            "name": proyecto,
            "files": 0, "size": 0, "clients": 0, "shrunk": 0,
            "last_ts": "",
            "regions": {},
        })
        p["files"] += count
        p["size"] += size
        if shrunk:
            p["shrunk"] += 1
        if newest_ts > p["last_ts"]:
            p["last_ts"] = newest_ts

        rkey = f"{entorno}/{pais}"
        rg = p["regions"].setdefault(rkey, {
            "entorno": entorno, "pais": pais,
            "files": 0, "size": 0, "shrunk": 0, "clients": {},
        })
        rg["files"] += count
        rg["size"] += size
        if shrunk:
            rg["shrunk"] += 1

        cli = rg["clients"].setdefault(label, {
            "label": label,
            "files": 0, "size": 0, "shrunk": 0,
            "last_ts": "",
            "monthly": None, "db": [],
        })
        cli["files"] += count
        cli["size"] += size
        if shrunk:
            cli["shrunk"] += 1
        if newest_ts > cli["last_ts"]:
            cli["last_ts"] = newest_ts
        if r["category"] == "os" and r["subkey"] == "linux":
            cli["monthly"] = entry
        else:
            cli["db"].append(entry)

    proyectos_out = []
    for pname in sorted(tree.keys()):
        p = tree[pname]
        regions_out = []
        cli_count = 0
        for rk in sorted(p["regions"].keys()):
            rg = p["regions"][rk]
            clients_list = []
            for clbl in sorted(rg["clients"].keys()):
                c = rg["clients"][clbl]
                c["db"].sort(key=lambda d: (d["subkey"], d["engine"]))
                clients_list.append(c)
            regions_out.append({
                "entorno": rg["entorno"], "pais": rg["pais"],
                "files": rg["files"], "size": rg["size"],
                "clients": clients_list,
            })
            cli_count += len(clients_list)
        p["clients"] = cli_count
        del p["regions"]
        proyectos_out.append({**p, "regions": regions_out})

    last = last_scan(conn)
    return {
        "summary": {
            "proyectos": len(proyectos_out),
            "clients": len(all_clients),
            "files": all_files,
            "size_bytes": all_size,
            "last_backup_ts": latest_ts_iso or None,
            "scanned_at": last["finished_unix"] if last else 0,
            "shrunk": shrunk_total,
            "shrink_pct_threshold": shrink_pct,
        },
        "proyectos": proyectos_out,
    }


def read_local_view(conn, *, proyecto: str, entorno: str, pais: str,
                    label: str, shrink_pct: int) -> dict:
    rows = conn.execute(
        f"SELECT {_LEAF_COLS} FROM drive_inventory "
        f"WHERE proyecto=? AND entorno=? AND pais=? AND label=? "
        f"ORDER BY category, subkey",
        (proyecto, entorno, pais, label),
    ).fetchall()
    leaf_ids = [r["id"] for r in rows]
    files_by_leaf = _files_grouped(conn, leaf_ids, cap=None)

    system_entry = None
    db_entries: list[dict] = []
    total_files = 0
    total_size = 0
    encrypted_files = 0
    shrunk_count = 0
    last_ts = ""
    last_path = ""
    last_crypto = ""

    for r in rows:
        entry = _row_to_entry(r, files_by_leaf.get(r["id"], []))
        total_files += entry["count"]
        total_size += entry["size"]
        encrypted_files += entry["encrypted_count"]
        if entry["shrunk"]:
            shrunk_count += 1
        if entry["newest_ts"] > last_ts:
            last_ts = entry["newest_ts"]
            last_path = entry["newest_path"]
            last_crypto = entry["newest_crypto"]
        if r["category"] == "os" and r["subkey"] == "linux":
            system_entry = entry
        else:
            db_entries.append(entry)

    db_entries.sort(key=lambda d: (d["subkey"], d["engine"]))

    last = last_scan(conn)
    return {
        "filter": {
            "proyecto": proyecto, "entorno": entorno,
            "pais": pais, "label": label,
        },
        "summary": {
            "files": total_files,
            "size_bytes": total_size,
            "encrypted_files": encrypted_files,
            "shrunk": shrunk_count,
            "shrink_pct_threshold": shrink_pct,
            "last_backup_ts": last_ts or None,
            "last_backup_path": last_path or None,
            "last_backup_crypto": last_crypto or None,
            "system_files": (system_entry["count"] if system_entry else 0),
            "system_size":  (system_entry["size"] if system_entry else 0),
            "db_count": len(db_entries),
            "db_files": sum(d["count"] for d in db_entries),
            "db_size":  sum(d["size"]  for d in db_entries),
            "scanned_at": last["finished_unix"] if last else 0,
        },
        "system": system_entry,
        "databases": db_entries,
    }


def last_scan(conn) -> dict | None:
    r = conn.execute(
        "SELECT id, started_at, finished_at, files_total, size_bytes_total, "
        "       leaves_total, duration_s, triggered_by "
        "FROM drive_scans WHERE status='ok' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if r is None:
        return None
    finished_unix = 0
    if r["finished_at"]:
        try:
            finished_unix = int(datetime.fromisoformat(
                r["finished_at"].replace("Z", "+00:00")
            ).timestamp())
        except Exception:
            finished_unix = 0
    return {
        "id": r["id"],
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
        "finished_unix": finished_unix,
        "files_total": r["files_total"],
        "size_bytes_total": r["size_bytes_total"],
        "leaves_total": r["leaves_total"],
        "duration_s": r["duration_s"],
        "triggered_by": r["triggered_by"],
    }
