"""One canonical object, two renderings: machine (stable compact IDs) and human (local time text)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..core.model import AvailabilityRule, Commitment, Occurrence, Recurrence, Task
from ..core.planner import Plan
from ..core.slots import Block, Slot

UTC = timezone.utc
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_ID = re.compile(r"^\s*([CPTR])?(\d+)\s*$", re.I)


def parse_id(value: str | int, prefix: str) -> int:
    """Accept 12, "12", or "C12" (prefix must match when given)."""
    if isinstance(value, int):
        return value
    m = _ID.match(str(value))
    if not m or (m.group(1) and m.group(1).upper() != prefix):
        raise ValueError(f"Invalid {prefix} id: {value!r}")
    return int(m.group(2))


def to_aware(dt: datetime, tz: ZoneInfo) -> datetime:
    """Naive input datetimes are interpreted in the user's local timezone."""
    return dt.replace(tzinfo=tz).astimezone(UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def local_iso(dt: datetime | None, tz: ZoneInfo) -> str | None:
    return None if dt is None else dt.astimezone(tz).isoformat()


def human_time(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def human_span(start: datetime, end: datetime, tz: ZoneInfo) -> str:
    s, e = start.astimezone(tz), end.astimezone(tz)
    day = s.strftime("%a %d %b")
    if s.date() == e.date():
        return f"{day} {s:%H:%M}–{e:%H:%M}"
    return f"{day} {s:%H:%M} – {e:%a %d %b %H:%M}"


def recurrence_dict(r: Recurrence | None, tz: ZoneInfo) -> dict | None:
    if r is None:
        return None
    return {"freq": r.freq.value, "interval": r.interval, "by_weekday": list(r.by_weekday),
            "until": local_iso(r.until, tz), "count": r.count, "text": recurrence_text(r)}


def recurrence_text(r: Recurrence) -> str:
    unit = {"daily": "day", "weekly": "week", "monthly": "month"}[r.freq.value]
    every = f"every {unit}" if r.interval == 1 else f"every {r.interval} {unit}s"
    if r.by_weekday:
        every += " on " + ", ".join(WEEKDAYS[d] for d in sorted(r.by_weekday))
    if r.count:
        every += f", {r.count} times"
    if r.until:
        every += f", until {r.until.date().isoformat()}"
    return every


def commitment(c: Commitment, tz: ZoneInfo, people: dict[int, str] | None = None) -> dict:
    people = people or {}
    return {
        "id": f"C{c.id}" if c.id is not None else None, "num_id": c.id, "title": c.title, "kind": c.kind,
        "start": local_iso(c.start, tz), "end": local_iso(c.end, tz),
        "when": human_span(c.start, c.end, tz),
        "status": c.status.value, "authority_level": c.authority_level,
        "people": [{"id": f"P{p}", "name": people.get(p, f"P{p}")} for p in c.person_ids],
        "location": c.location, "notes": c.notes,
        "buffer_before_min": c.buffer_before_min, "buffer_after_min": c.buffer_after_min,
        "recurrence": recurrence_dict(c.recurrence, tz),
        "reminders": None if c.reminders is None else [
            {"offset_min": r.offset_min, "at": local_iso(r.at, tz), "label": r.label} for r in c.reminders],
        "task_id": f"T{c.task_id}" if c.task_id else None,
    }


def occurrence(o: Occurrence, tz: ZoneInfo, people: dict[int, str] | None = None) -> dict:
    c = o.commitment
    people = people or {}
    return {
        "key": o.key, "commitment_id": f"C{c.id}", "title": c.title, "kind": c.kind,
        "start": local_iso(o.start, tz), "end": local_iso(o.end, tz),
        "when": human_span(o.start, o.end, tz), "status": c.status.value,
        "authority_level": c.authority_level,
        "people": [people.get(p, f"P{p}") for p in c.person_ids],
        "location": c.location, "recurring": c.recurrence is not None,
        "buffer_before_min": c.buffer_before_min, "buffer_after_min": c.buffer_after_min,
        "task_id": f"T{c.task_id}" if c.task_id else None,
    }


def slot(s: Slot, tz: ZoneInfo) -> dict:
    return {"start": local_iso(s.start, tz), "end": local_iso(s.end, tz),
            "when": human_span(s.start, s.end, tz), "score": s.score, "reasons": s.reasons}


def block(b: Block, tz: ZoneInfo) -> dict:
    return {"start": local_iso(b.start, tz), "end": local_iso(b.end, tz),
            "text": f"{human_time(b.start, tz)}–{human_time(b.end, tz)}  {b.label or b.kind.capitalize()}",
            "kind": b.kind, "label": b.label, "key": b.occurrence_key,
            **({"commitment_id": f"C{b.occurrence.commitment.id}", "status": b.occurrence.commitment.status.value,
                "authority_level": b.occurrence.commitment.authority_level,
                "buffer_before_min": b.occurrence.commitment.buffer_before_min,
                "buffer_after_min": b.occurrence.commitment.buffer_after_min,
                "recurring": b.occurrence.commitment.recurrence is not None} if b.occurrence else {})}


def rule(r: AvailabilityRule) -> dict:
    return {"id": f"R{r.id}", "num_id": r.id, "kind": r.kind.value, "weekdays": list(r.weekdays),
            "start": f"{r.start_minute // 60:02d}:{r.start_minute % 60:02d}",
            "end": f"{r.end_minute // 60:02d}:{r.end_minute % 60:02d}",
            "applies_to": r.applies_to, "label": r.label, "weight": r.weight}


def task(t: Task, tz: ZoneInfo, status=None) -> dict:
    d = {"id": f"T{t.id}", "num_id": t.id, "title": t.title, "deadline": local_iso(t.deadline, tz),
         "deadline_text": t.deadline.astimezone(tz).strftime("%a %d %b %H:%M"),
         "estimated_duration_min": t.estimated_duration_min, "status": t.status, "notes": t.notes}
    if status is not None:
        d.update(scheduled_min=status.scheduled_min, completed_min=status.completed_min,
                 remaining_min=status.remaining_min, free_min_before_deadline=status.free_min_before_deadline,
                 at_risk=status.at_risk, overdue=status.overdue)
    return d


def person(p: dict) -> dict:
    d = dict(p)
    d["num_id"] = p["id"]
    d["id"] = f"P{p['id']}"
    return d


def plan(p: Plan, tz: ZoneInfo, people: dict[int, str] | None = None) -> dict:
    report = p.report
    conflicts = []
    if report is not None:
        for group in (report.blocked, report.equal, report.overridable):
            for item in group:
                d = occurrence(item.occurrence, tz, people)
                d["relation"] = item.relation
                conflicts.append(d)
    return {
        "action": p.action, "outcome": p.outcome, "executable": p.executable,
        "confirmation": p.confirmation, "message": p.message,
        "candidate": commitment(p.candidate, tz, people) if p.candidate else None,
        "conflicts": conflicts,
        "displace": [occurrence(o, tz, people) for o in p.displace],
        "cancel": [commitment(c, tz, people) for c in p.cancel],
        "alternatives": [slot(s, tz) for s in p.alternatives],
        "warnings": p.warnings, "options": p.options, "typed_phrase": p.typed_phrase,
    }
