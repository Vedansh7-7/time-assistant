"""SQLite backups.

Uses SQLite's online backup API, so a consistent snapshot is taken while the
server keeps running. Snapshots go to the local backup dir and, if configured,
are copied to `target_dir`: a path where the backup device is mounted (for
example an SMB/NFS share mounted with the device's admin credentials; see
deploy/README.md). Credentials therefore never live in this app's database.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import settings as settings_mod

log = logging.getLogger("backup")
PREFIX = "time_assistant-"


def snapshot(conn: sqlite3.Connection, backup_dir: Path, keep: int, target_dir: str = "") -> dict:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"{PREFIX}{stamp}.db"
    out = sqlite3.connect(str(dest))
    try:
        conn.backup(out)
        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        out.close()
    if ok != "ok":
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"backup integrity check failed: {ok}")
    copied = None
    if target_dir:
        target = Path(target_dir)
        if not target.is_dir():
            raise RuntimeError(f"backup target {target} is not mounted/available (local snapshot kept)")
        shutil.copy2(dest, target / dest.name)
        copied = str(target / dest.name)
        _rotate(target, keep)
    _rotate(backup_dir, keep)
    return {"file": str(dest), "copied_to": copied, "size": dest.stat().st_size, "at": stamp}


def _rotate(folder: Path, keep: int) -> None:
    files = sorted(folder.glob(f"{PREFIX}*.db"))
    for f in files[:-keep] if keep > 0 else []:
        f.unlink(missing_ok=True)


def list_backups(backup_dir: Path) -> list[dict]:
    if not backup_dir.exists():
        return []
    return [{"file": f.name, "size": f.stat().st_size} for f in sorted(backup_dir.glob(f"{PREFIX}*.db"), reverse=True)]


def last_backup_time(backup_dir: Path) -> datetime | None:
    files = sorted(backup_dir.glob(f"{PREFIX}*.db")) if backup_dir.exists() else []
    if not files:
        return None
    stamp = files[-1].stem[len(PREFIX):]
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


async def run_loop(open_conn, backup_dir: Path, state: dict, check_every_s: int = 600) -> None:
    while True:
        try:
            conn = open_conn()
            try:
                cfg = settings_mod.get(conn, "backup")
                last = last_backup_time(backup_dir)
                due = last is None or datetime.now(timezone.utc) - last >= timedelta(hours=float(cfg["interval_hours"]))
                if cfg["enabled"] and due:
                    result = await asyncio.to_thread(snapshot, conn, backup_dir, int(cfg["keep"]), cfg["target_dir"])
                    state.update(last_result=result, last_error=None)
                    log.info("backup written: %s", result["file"])
            finally:
                conn.close()
        except Exception as e:
            state["last_error"] = str(e)
            log.exception("backup failed")
        await asyncio.sleep(check_every_s)
