"""Deterministic reminder engine. No AI is involved at fire time.

Reminder specs live on commitments (per-commitment override) or in settings
(global defaults). Fire times are computed from the *current* schedule every
tick, so moving or cancelling a commitment automatically moves or drops its
reminders. `reminder_log` deduplicates.
"""
from __future__ import annotations

import asyncio
import logging
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..core.model import Occurrence, ReminderSpec
from ..core.recurrence import expand
from ..db import repo
from ..db.connection import write_tx
from . import serialize as ser
from . import settings as settings_mod

log = logging.getLogger("reminders")


@dataclass
class Due:
    occurrence: Occurrence
    fire_at: datetime
    message: str


def specs_for(c, defaults: list[int]) -> tuple[ReminderSpec, ...]:
    if c.reminders is not None:
        return c.reminders
    return tuple(ReminderSpec(offset_min=m) for m in defaults)


def upcoming(conn, tz, now: datetime, horizon: timedelta = timedelta(days=2)) -> list[Due]:
    """All reminder fire times for occurrences in [now - grace, now + horizon]."""
    cfg = settings_mod.get(conn, "reminders")
    defaults = [int(x) for x in cfg["default_offsets_min"]]
    grace = timedelta(hours=float(cfg["grace_hours"]))
    max_offset = timedelta(minutes=max([0, *defaults]))
    out: list[Due] = []
    for c in repo.active_commitments(conn):
        specs = specs_for(c, defaults)
        if not specs:
            continue
        reach = max_offset
        for s in specs:
            if s.offset_min is not None:
                reach = max(reach, timedelta(minutes=s.offset_min))
        for occ in expand(c, now - grace, now + horizon + reach, tz):
            for s in specs:
                if s.at is not None:
                    if c.recurrence is not None:
                        continue  # absolute reminders only make sense for one-offs
                    fire = s.at
                else:
                    fire = occ.start - timedelta(minutes=s.offset_min)
                label = s.label or (f"in {s.offset_min} min" if s.offset_min else "now")
                msg = f"{c.title} {label}, {ser.human_span(occ.start, occ.end, tz)}"
                out.append(Due(occ, fire, msg))
    out.sort(key=lambda d: d.fire_at)
    return out


def fire_due(conn, tz, now: datetime) -> list[Due]:
    """Mark due reminders as fired and return them. Idempotent."""
    grace = timedelta(hours=float(settings_mod.get(conn, "reminders")["grace_hours"]))
    fired = []
    with write_tx(conn):
        for d in upcoming(conn, tz, now):
            if d.fire_at > now or d.fire_at < now - grace or d.occurrence.end < now:
                continue
            if repo.reminder_fired(conn, d.occurrence.key, d.fire_at):
                continue
            repo.log_reminder(conn, d.occurrence.key, d.fire_at, d.occurrence.commitment.id, d.message)
            fired.append(d)
    return fired


def _push_ntfy(url: str, message: str) -> None:
    req = urllib.request.Request(url, data=message.encode(), method="POST",
                                 headers={"Title": "Reminder", "Tags": "alarm_clock"})
    urllib.request.urlopen(req, timeout=10).read()


async def run_loop(open_conn, now_fn, interval_s: int = 30) -> None:
    """Background loop: fire reminders; push to ntfy if configured. Survives errors."""
    while True:
        try:
            conn = open_conn()
            try:
                tz = settings_mod.tz(conn)
                fired = fire_due(conn, tz, now_fn())
                url = settings_mod.get(conn, "reminders").get("ntfy_url")
            finally:
                conn.close()
            for d in fired:
                log.info("reminder: %s", d.message)
                if url:
                    try:
                        await asyncio.to_thread(_push_ntfy, url, d.message)
                    except Exception as e:  # offline is normal; in-app list still has it
                        log.warning("ntfy push failed: %s", e)
        except Exception:
            log.exception("reminder loop error")
        await asyncio.sleep(interval_s)


def preview(conn, tz, c, now: datetime) -> list[dict]:
    """Reminder times for a commitment, for display/AI explanation."""
    defaults = [int(x) for x in settings_mod.get(conn, "reminders")["default_offsets_min"]]
    out = []
    for occ in expand(c, now, now + timedelta(days=30), tz)[:3]:
        for s in specs_for(c, defaults):
            fire = s.at if s.at is not None else occ.start - timedelta(minutes=s.offset_min)
            if fire >= now:
                out.append({"occurrence": occ.key, "fire_at": ser.local_iso(fire, tz),
                            "text": fire.astimezone(tz).strftime("%a %d %b %H:%M")})
    return out

