"""Parser puro para DB_BACKUP_TARGETS.

Format: "engine:dbname engine:dbname ..." (space-separated).
Engine: one of VALID_ENGINES.
DB name: alphanumeric + dash + underscore + dot. Anything else → ParseError.
"""
from __future__ import annotations

import re
from typing import List, Tuple

VALID_ENGINES = ("postgres", "mysql", "mongo")
_DBNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class ParseError(ValueError):
    pass


def parse_targets(s: str) -> List[Tuple[str, str]]:
    """Returns [(engine, dbname), ...]. Empty string → []."""
    s = (s or "").strip()
    if not s:
        return []
    out: list[tuple[str, str]] = []
    for tok in s.split():
        if ":" not in tok:
            raise ParseError(f"target malformado (falta ':'): {tok!r}")
        engine, _, dbname = tok.partition(":")
        engine = engine.strip().lower()
        dbname = dbname.strip()
        if engine not in VALID_ENGINES:
            raise ParseError(
                f"engine no soportado: {engine!r} (válidos: {VALID_ENGINES})"
            )
        if not dbname:
            raise ParseError(f"target sin dbname: {tok!r}")
        if not _DBNAME_RE.match(dbname):
            raise ParseError(
                f"dbname inválido: {dbname!r} (solo alfanum + ._-)"
            )
        out.append((engine, dbname))
    return out
