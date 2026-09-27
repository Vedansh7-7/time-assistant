"""Read-only scheduling queries: availability, slot search, tasks, dashboard."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..core.slots import SlotRequest, day_view, find_slots
from ..core.tasks import propose_work_blocks, task_status
from ..db import repo
from . import serialize as ser
from .core import CoreService, ServiceError


def find_available_slots(svc: CoreService, duration_min: int, start=None, end=None, kind: str = "meeting",
                         buffer_before_min: int = 0, buffer_after_min: int = 0, near=None,
                         limit: int | None = None, exclude_id=None) -> list[dict]:
    if duration_min <= 0 or duration_min > 24 * 60:
        raise ServiceError("INVALID", "duration_min must be 1–1440")
    now = svc.now()
    s = svc._dt(start) if start else now
    e = svc._dt(end) if end else s + timedelta(days=svc.search.horizon_days)
    if e <= s:
        raise ServiceError("INVALID_TIME", "end must be after start")
    exclude = frozenset({ser.parse_id(exclude_id, "C")}) if exclude_id else frozenset()
    req = SlotRequest(duration_min, s, e, kind, buffer_before_min, buffer_after_min,
                      svc._dt(near) if near else None, exclude)
    slots = find_slots(req, repo.active_commitments(svc.conn), repo.list_rules(svc.conn), svc.tz, now,
                       svc.search, limit)
    return [ser.slot(x, svc.tz) for x in slots]


def day(svc: CoreService, d: date) -> dict:
    blocks = day_view(d, repo.active_commitments(svc.conn), repo.list_rules(svc.conn), svc.tz, svc.search)
    return {"date": d.isoformat(), "blocks": [ser.block(b, svc.tz) for b in blocks],
            "text": "\n".join(ser.block(b, svc.tz)["text"] for b in blocks)}


def local_day_bounds(svc: CoreService, d: date) -> tuple[datetime, datetime]:
    start = datetime(d.year, d.month, d.day, tzinfo=svc.tz)
    return ser.to_aware(start, svc.tz), ser.to_aware(start + timedelta(days=1), svc.tz)


def tasks(svc: CoreService, include_closed: bool = False) -> list[dict]:
    commitments = repo.list_commitments(svc.conn)
    rules = repo.list_rules(svc.conn)
    now = svc.now()
    out = []
    for t in repo.list_tasks(svc.conn, None if include_closed else ["open"]):
        st = task_status(t, commitments, rules, svc.tz, now, svc.search) if t.status == "open" else None
        out.append(ser.task(t, svc.tz, st))
    return out


def task_detail(svc: CoreService, tid) -> dict:
    t = repo.get_task(svc.conn, ser.parse_id(tid, "T"))
    if t is None:
        raise ServiceError("NOT_FOUND", f"Task {tid} not found", 404)
    commitments = repo.list_commitments(svc.conn)
    st = task_status(t, commitments, repo.list_rules(svc.conn), svc.tz, svc.now(), svc.search)
    d = ser.task(t, svc.tz, st)
    d["blocks"] = [ser.commitment(c, svc.tz) for c in commitments if c.task_id == t.id]
    return d


def work_block_options(svc: CoreService, tid) -> dict:
    t = repo.get_task(svc.conn, ser.parse_id(tid, "T"))
    if t is None:
        raise ServiceError("NOT_FOUND", f"Task {tid} not found", 404)
    commitments = repo.active_commitments(svc.conn)
    all_c = repo.list_commitments(svc.conn)
    rules = repo.list_rules(svc.conn)
    now = svc.now()
    st = task_status(t, all_c, rules, svc.tz, now, svc.search)
    plans = propose_work_blocks(t, st.remaining_min, commitments, rules, svc.tz, now, svc.search)
    return {"task": ser.task(t, svc.tz, st),
            "options": [[ser.slot(s, svc.tz) for s in plan] for plan in plans],
            "message": ("No remaining work to schedule." if st.remaining_min == 0 else
                        "Not enough free time before the deadline." if not plans else
                        f"{st.remaining_min} min remaining.")}


def dashboard(svc: CoreService) -> dict:
    now = svc.now()
    today = now.astimezone(svc.tz).date()
    s, e = local_day_bounds(svc, today)
    upcoming_end = e + timedelta(days=7)
    task_list = tasks(svc)
    return {
        "now": ser.local_iso(now, svc.tz),
        "timezone": str(svc.tz),
        "today": svc.schedule(s, e),
        "today_view": day(svc, today),
        "upcoming": [o for o in svc.schedule(e, upcoming_end)][:20],
        "tasks": task_list,
        "at_risk_tasks": [t for t in task_list if t.get("at_risk")],
        "needs_reschedule": svc.attention(),
        "notifications": repo.active_notifications(svc.conn),
        "pending": svc.pending(),
        "open_requests": svc.conn.execute(
            "SELECT COUNT(*) FROM public_requests WHERE status = 'requested'").fetchone()[0],
    }
