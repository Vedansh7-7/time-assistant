"""SQLite connections.

* WAL journal: readers never block the writer; gentler on SD cards.
* Writes use BEGIN IMMEDIATE so plan-validation and apply happen atomically:
  a plan is re-validated inside the same write transaction that applies it.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .schema import MIGRATIONS


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _split(sql):
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {i}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _split(sql: str) -> list[str]:
    out, buf = [], []
    for line in sql.splitlines():
        clean = line.split("--", 1)[0]
        buf.append(clean)
        if clean.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
    rest = "\n".join(buf).strip()
    if rest:
        out.append(rest)
    return out


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
