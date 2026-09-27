"""Alternative-slot search and availability.

Pipeline: candidate generation -> constraint filtering (occupied, hard rules,
past) -> preference scoring -> diversity selection -> ranked alternatives.
A lower score is better. Every result carries human-readable reasons.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .conflicts import OccupancyIndex, overlaps
from .model import AvailabilityRule, Commitment, Occurrence, RuleKind, SearchSettings
from .rules import applies, overlap_minutes, rule_windows

UTC = timezone.utc


@dataclass
class Slot:
    start: datetime
    end: datetime
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class SlotRequest:
    duration_min: int
    search_start: datetime
    search_end: datetime
    kind: str = "meeting"
    buffer_before_min: int = 0
    buffer_after_min: int = 0
    requested_start: datetime | None = None  # rank alternatives near this time
    exclude_ids: frozenset[int] = frozenset()  # e.g. the commitment being moved


def _day_bounds(d, tz: ZoneInfo, settings: SearchSettings) -> tuple[datetime, datetime]:
    midnight = datetime.combine(d, time(0), tzinfo=tz)
    return (
        (midnight + timedelta(minutes=settings.day_start_minute)).astimezone(UTC),
        (midnight + timedelta(minutes=settings.day_end_minute)).astimezone(UTC),
    )


def _score(start: datetime, end: datetime, req: SlotRequest, rules: list[AvailabilityRule],
           tz: ZoneInfo) -> tuple[float, list[str]] | None:
    score = 0.0
    reasons: list[str] = []
    for r in rules:
        if not applies(r, req.kind):
            continue
        mins = overlap_minutes(r, start, end, tz)
        if not mins:
            continue
        label = r.label or r.kind.value
        if r.kind == RuleKind.BLOCK:
            return None
        if r.kind == RuleKind.AVOID:
            score += 2.0 * mins * r.weight
            reasons.append(f"inside an 'avoid' window ({label})")
        elif r.kind == RuleKind.PREFER:
            score -= 1.0 * mins * r.weight
            reasons.append(f"preferred time ({label})")
    if req.requested_start is not None:
        hours_away = abs((start - req.requested_start).total_seconds()) / 3600
        score += hours_away * 2.0
        if start.astimezone(tz).date() == req.requested_start.astimezone(tz).date():
            reasons.append("same day as requested")
    else:
        score += (start - req.search_start).total_seconds() / 3600 * 0.25
    return score, reasons


def find_slots(req: SlotRequest, existing: list[Commitment], rules: list[AvailabilityRule],
               tz: ZoneInfo, now: datetime, settings: SearchSettings = SearchSettings(),
               limit: int | None = None) -> list[Slot]:
    """Best non-overlapping slots, ranked."""
    limit = limit or settings.max_results
    candidates = valid_slots(req, existing, rules, tz, now, settings)
    candidates.sort(key=lambda sl: (sl.score, sl.start))
    picked: list[Slot] = []
    for sl in candidates:
        if any(overlaps(sl.start, sl.end, p.start, p.end) for p in picked):
            continue
        picked.append(sl)
        if len(picked) >= limit:
            break
    return picked


def valid_slots(req: SlotRequest, existing: list[Commitment], rules: list[AvailabilityRule],
                tz: ZoneInfo, now: datetime, settings: SearchSettings = SearchSettings()) -> list[Slot]:
    """Every usable start time (step-aligned in local time), scored, in time order."""
    duration = timedelta(minutes=req.duration_min)
    pad_b = timedelta(minutes=req.buffer_before_min)
    pad_a = timedelta(minutes=req.buffer_after_min)
    lower = max(req.search_start, now)
    if lower >= req.search_end:
        return []
    index = OccupancyIndex(existing, lower - pad_b - timedelta(days=1), req.search_end + pad_a + timedelta(days=1),
                           tz, req.exclude_ids)
    step = timedelta(minutes=settings.step_min)

    candidates: list[Slot] = []
    d = lower.astimezone(tz).date()
    last = req.search_end.astimezone(tz).date()
    while d <= last:
        day_start, day_end = _day_bounds(d, tz, settings)
        t = day_start
        while t + duration <= day_end:
            s, e = t, t + duration
            t += step
            if s < lower or e > req.search_end:
                continue
            if not index.is_free(s - pad_b, e + pad_a):
                continue
            scored = _score(s, e, req, rules, tz)
            if scored is None:
                continue
            candidates.append(Slot(s, e, round(scored[0], 2), scored[1]))
        d += timedelta(days=1)
    return candidates


@dataclass
class Block:
    start: datetime
    end: datetime
    kind: str  # busy | free | blocked
    label: str = ""
    occurrence_key: str | None = None
    occurrence: "Occurrence | None" = None


def day_view(d, existing: list[Commitment], rules: list[AvailabilityRule], tz: ZoneInfo,
             settings: SearchSettings = SearchSettings()) -> list[Block]:
    """Busy/free/blocked timeline for one local day. Free time is inferred, not stored."""
    ds, de = _day_bounds(d, tz, settings)
    index = OccupancyIndex(existing, ds - timedelta(days=1), de + timedelta(days=1), tz)
    busy = [o for o in index.overlapping(ds, de)]
    blocks: list[Block] = []
    cursor = ds
    for o in busy:
        if o.start > cursor:
            blocks.extend(_free_segments(cursor, min(o.start, de), rules, tz))
        blocks.append(Block(o.start, o.end, "busy", o.commitment.title, o.key, o))
        cursor = max(cursor, o.end)
    if cursor < de:
        blocks.extend(_free_segments(cursor, de, rules, tz))
    return blocks


def _free_segments(start: datetime, end: datetime, rules: list[AvailabilityRule], tz: ZoneInfo) -> list[Block]:
    """Split a free gap where hard 'block' rules apply (free ≠ usable)."""
    cuts = [(start, end, "free", "")]
    for r in rules:
        if r.kind != RuleKind.BLOCK:
            continue
        for ws, we in rule_windows(r, start, end, tz):
            new = []
            for s, e, k, lbl in cuts:
                if k != "free" or not overlaps(s, e, ws, we):
                    new.append((s, e, k, lbl))
                    continue
                if s < ws:
                    new.append((s, ws, "free", ""))
                new.append((max(s, ws), min(e, we), "blocked", r.label or "unavailable"))
                if we < e:
                    new.append((we, e, "free", ""))
            cuts = new
    return [Block(s, e, k, lbl) for s, e, k, lbl in cuts if e > s]
