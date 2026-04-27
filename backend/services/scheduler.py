"""Gestión de timers systemd y retención (políticas de prune).

Diseñado para correr como root: escribe drop-ins en
`/etc/systemd/system/snapshot@<unit>.timer.d/override.conf` y
edita `/opt/snapshot-V3/core/etc/snapshot.conf` preservando el resto.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path

# Unidades systemd soportadas (instancias del template snapshot@.timer).
# `archive` es el único timer activo por defecto (cold-storage mensual);
# `create` y `prune` son del flujo restic legacy y solo aplican si el
# operador los reactiva manualmente. Mantener `archive` aquí permite que
# la UI edite su horario sin tocar el drop-in a mano.
SUPPORTED_UNITS = {"archive", "create", "prune"}

# Defaults razonables si no hay drop-in.
_DEFAULT_ONCALENDAR = {
    "archive": "*-*-01 02:00:00",   # día 1 del mes 02:00 UTC (matches install.sh)
    "create":  "*-*-* 03:00:00",
    "prune":   "*-*-* 04:00:00",
}
_DEFAULT_DELAY = "30min"

DROPIN_DIR = Path("/etc/systemd/system")
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class ScheduleError(Exception):
    pass


@dataclass
class Schedule:
    """Representación amigable para la UI."""
    unit: str
    enabled: bool
    kind: str            # hourly | daily | weekly | monthly | custom
    time: str            # "HH:MM"  (vacío para hourly/custom)
    weekday: str         # "Mon".."Sun" (solo weekly)
    day: int             # 1..31 (solo monthly)
    oncalendar: str      # expresión real cargada en systemd
    next_run: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------- helpers OnCalendar ----------------
def _parse_hhmm(s: str) -> tuple[int, int]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", s.strip())
    if not m:
        raise ScheduleError(f"hora inválida: {s!r} (usa HH:MM)")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ScheduleError(f"hora fuera de rango: {s!r}")
    return h, mi


def build_oncalendar(kind: str, time: str = "", weekday: str = "Mon", day: int = 1) -> str:
    kind = (kind or "daily").lower()
    if kind == "hourly":
        return "*-*-* *:00:00"
    if kind == "daily":
        h, mi = _parse_hhmm(time or "03:00")
        return f"*-*-* {h:02d}:{mi:02d}:00"
    if kind == "weekly":
        if weekday not in WEEKDAY_NAMES:
            raise ScheduleError(f"weekday inválido: {weekday!r}")
        h, mi = _parse_hhmm(time or "03:00")
        return f"{weekday} *-*-* {h:02d}:{mi:02d}:00"
    if kind == "monthly":
        if not (1 <= int(day) <= 31):
            raise ScheduleError("day debe estar entre 1 y 31")
        h, mi = _parse_hhmm(time or "03:00")
        return f"*-*-{int(day):02d} {h:02d}:{mi:02d}:00"
    raise ScheduleError(f"kind no soportado: {kind!r}")


def parse_oncalendar(expr: str) -> dict:
    """Intenta parsear una expresión OnCalendar al modelo amigable.
    Si no encaja en ningún preset → kind='custom'.
    """
    s = (expr or "").strip()
    out = {"kind": "custom", "time": "", "weekday": "Mon", "day": 1}
    # hourly
    if re.fullmatch(r"\*-\*-\*\s+\*:00:00", s) or s == "hourly":
        out["kind"] = "hourly"
        return out
    # daily
    m = re.fullmatch(r"\*-\*-\*\s+(\d{2}):(\d{2}):00", s)
    if m:
        out["kind"] = "daily"
        out["time"] = f"{m.group(1)}:{m.group(2)}"
        return out
    # weekly
    m = re.fullmatch(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\*-\*-\*\s+(\d{2}):(\d{2}):00", s)
    if m:
        out["kind"] = "weekly"
        out["weekday"] = m.group(1)
        out["time"] = f"{m.group(2)}:{m.group(3)}"
        return out
    # monthly
    m = re.fullmatch(r"\*-\*-(\d{2})\s+(\d{2}):(\d{2}):00", s)
    if m:
        out["kind"] = "monthly"
        out["day"] = int(m.group(1))
        out["time"] = f"{m.group(2)}:{m.group(3)}"
        return out
    return out


def validate_oncalendar(expr: str) -> None:
    """Lanza ScheduleError si systemd-analyze rechaza la expresión."""
    if not expr or len(expr) > 256:
        raise ScheduleError("OnCalendar vacío o demasiado largo")
    # Caracteres permitidos en expresiones systemd: letras, dígitos, espacios, *, /, -, :, ,
    if not re.fullmatch(r"[A-Za-z0-9 *,:/\-]+", expr):
        raise ScheduleError("OnCalendar contiene caracteres no permitidos")
    try:
        r = subprocess.run(
            ["systemd-analyze", "calendar", expr],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError as e:
        raise ScheduleError(f"systemd-analyze no disponible: {e}")
    except subprocess.TimeoutExpired:
        raise ScheduleError("systemd-analyze tardó demasiado")
    if r.returncode != 0:
        raise ScheduleError(f"OnCalendar inválido: {r.stderr.strip() or r.stdout.strip()}")


# ---------------- helpers systemd ----------------
def _unit_assert(unit: str) -> None:
    if unit not in SUPPORTED_UNITS:
        raise ScheduleError(f"unidad no soportada: {unit!r}")


def _dropin_path(unit: str) -> Path:
    return DROPIN_DIR / f"snapshot@{unit}.timer.d" / "override.conf"


def _read_dropin_oncalendar(unit: str) -> tuple[str, str]:
    """Devuelve (oncalendar, randomized_delay). Vacíos si no hay drop-in."""
    p = _dropin_path(unit)
    if not p.exists():
        return "", ""
    try:
        text = p.read_text()
    except OSError:
        return "", ""
    oncal = ""
    delay = ""
    # Pueden coexistir varias líneas OnCalendar; cogemos la última no-vacía
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("OnCalendar="):
            v = line.split("=", 1)[1].strip()
            if v:
                oncal = v
        elif line.startswith("RandomizedDelaySec="):
            delay = line.split("=", 1)[1].strip()
    return oncal, delay


def _write_dropin(unit: str, oncalendar: str, delay: str) -> None:
    p = _dropin_path(unit)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# Generado por snapshot-V3 (panel web)\n"
        "[Timer]\n"
        "OnCalendar=\n"           # vacío resetea heredados del template
        f"OnCalendar={oncalendar}\n"
        f"RandomizedDelaySec={delay or _DEFAULT_DELAY}\n"
        "Persistent=true\n"
    )
    # Escritura atómica: tmp → rename
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(p.parent), prefix=".override.")
    try:
        tmp.write(body)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.chmod(tmp.name, 0o644)
        os.replace(tmp.name, p)
    except Exception:
        try: os.unlink(tmp.name)
        except OSError: pass
        raise


def _systemctl(*args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=20)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "systemctl timeout"


def _next_run(unit: str) -> str | None:
    rc, out = _systemctl("show", f"snapshot@{unit}.timer", "--property=NextElapseUSecRealtime,NextElapseUSec")
    if rc != 0:
        return None
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            v = v.strip()
            if v and v != "n/a" and v != "0":
                return v
    return None


def _read_effective_oncalendar(unit: str) -> str:
    """Devuelve la expresión OnCalendar *realmente* cargada por systemd.

    Esto incluye los valores del template (`snapshot@.timer`) y de
    cualquier drop-in. Sin esto, la UI mostraría el default falso
    (03:00) del backend aunque systemd esté disparando a otra hora.
    """
    rc, out = _systemctl(
        "show", f"snapshot@{unit}.timer", "--property=TimersCalendar"
    )
    if rc != 0 or not out:
        return ""
    # Formato: TimersCalendar={ OnCalendar=<expr> ; next_elapse=<ts> }
    # Puede haber varias líneas TimersCalendar= si se definen múltiples
    # OnCalendar — nos quedamos con la última no-vacía, que es la efectiva
    # tras los overrides (systemd concatena, pero los OnCalendar= vacíos
    # resetean la lista, que es como los escribimos en el drop-in).
    last = ""
    for line in out.splitlines():
        if not line.startswith("TimersCalendar="):
            continue
        m = re.search(r"OnCalendar=([^;}]+?)\s*;", line)
        if m:
            expr = m.group(1).strip()
            if expr:
                last = expr
    return last


def _is_enabled(unit: str) -> bool:
    rc, _ = _systemctl("is-enabled", f"snapshot@{unit}.timer")
    return rc == 0


# ---------------- API pública ----------------
def get_schedule(unit: str) -> Schedule:
    _unit_assert(unit)
    # Preferencia: lo que systemd realmente tiene cargado (template + drop-in).
    # Fallback: lee el drop-in a mano por si systemctl no está disponible.
    # Último fallback: default hardcodeado (solo si no hay systemd ni drop-in).
    oncal = _read_effective_oncalendar(unit)
    if not oncal:
        oncal, _ = _read_dropin_oncalendar(unit)
    if not oncal:
        oncal = _DEFAULT_ONCALENDAR.get(unit, "*-*-* 03:00:00")
    parsed = parse_oncalendar(oncal)
    return Schedule(
        unit=unit,
        enabled=_is_enabled(unit),
        kind=parsed["kind"],
        time=parsed["time"],
        weekday=parsed["weekday"],
        day=int(parsed["day"]),
        oncalendar=oncal,
        next_run=_next_run(unit),
    )


def set_schedule(
    unit: str,
    *,
    kind: str,
    time: str = "",
    weekday: str = "Mon",
    day: int = 1,
    oncalendar: str = "",
    delay: str = "",
    enabled: bool = True,
) -> Schedule:
    _unit_assert(unit)
    if kind == "custom":
        expr = (oncalendar or "").strip()
        if not expr:
            raise ScheduleError("Falta la expresión OnCalendar para 'custom'")
    else:
        expr = build_oncalendar(kind, time=time, weekday=weekday, day=int(day or 1))

    validate_oncalendar(expr)

    # Validar delay si viene
    delay_clean = (delay or _DEFAULT_DELAY).strip()
    if not re.fullmatch(r"\d+(ms|s|min|h|d)?", delay_clean):
        raise ScheduleError(f"RandomizedDelaySec inválido: {delay_clean!r}")

    _write_dropin(unit, expr, delay_clean)

    rc, out = _systemctl("daemon-reload")
    if rc != 0:
        raise ScheduleError(f"daemon-reload falló: {out}")

    timer = f"snapshot@{unit}.timer"
    if enabled:
        rc, out = _systemctl("enable", "--now", timer)
        if rc != 0 and "already" not in out.lower():
            raise ScheduleError(f"enable falló: {out}")
        rc, out = _systemctl("restart", timer)
        if rc != 0:
            raise ScheduleError(f"restart timer falló: {out}")
    else:
        _systemctl("disable", "--now", timer)

    return get_schedule(unit)


def list_schedules() -> list[dict]:
    return [get_schedule(u).to_dict() for u in sorted(SUPPORTED_UNITS)]


# ---------------- Retención ----------------
RETENTION_KEYS = ("KEEP_DAILY", "KEEP_WEEKLY", "KEEP_MONTHLY", "KEEP_YEARLY")


def _conf_path() -> Path:
    return Path(os.getenv("CONF_FILE", "/opt/snapshot-V3/core/etc/snapshot.conf"))


def get_retention() -> dict:
    p = _conf_path()
    out = {k.lower(): 0 for k in RETENTION_KEYS}
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            for k in RETENTION_KEYS:
                m = re.match(rf'^{k}\s*=\s*"?(\d+)"?\s*(?:#.*)?$', line)
                if m:
                    out[k.lower()] = int(m.group(1))
    except OSError:
        pass
    return out


def set_retention(values: dict) -> dict:
    p = _conf_path()
    if not p.exists():
        raise ScheduleError(f"snapshot.conf no encontrado: {p}")

    # Validar y normalizar
    new_vals: dict[str, int] = {}
    for k in RETENTION_KEYS:
        raw = values.get(k.lower(), values.get(k))
        if raw is None:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ScheduleError(f"{k} debe ser un entero")
        if not (0 <= n <= 9999):
            raise ScheduleError(f"{k} fuera de rango (0-9999)")
        new_vals[k] = n
    if not new_vals:
        raise ScheduleError("no se enviaron valores de retención")

    text = p.read_text()
    lines = text.splitlines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        for k, v in new_vals.items():
            if re.match(rf'^\s*{k}\s*=', line):
                lines[i] = f"{k}={v}"
                seen.add(k)
                break
    # Si alguna no existía en el fichero, la añadimos al final
    for k, v in new_vals.items():
        if k not in seen:
            lines.append(f"{k}={v}")

    new_text = "\n".join(lines)
    if not new_text.endswith("\n"):
        new_text += "\n"

    # Backup + escritura atómica
    bak = p.with_suffix(p.suffix + ".bak")
    try:
        shutil.copy2(p, bak)
    except OSError:
        pass

    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(p.parent), prefix=".snapshot.conf.")
    try:
        tmp.write(new_text)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.chmod(tmp.name, 0o644)
        os.replace(tmp.name, p)
    except Exception:
        try: os.unlink(tmp.name)
        except OSError: pass
        raise

    return get_retention()
