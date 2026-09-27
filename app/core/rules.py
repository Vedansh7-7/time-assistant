"""Availability rules (explicit availability/preferences layered over inferred free time)."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .model import AvailabilityRule, RuleKind

UTC = timezone.utc


def _local_midnight(d, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz)


def rule_windows(rule: AvailabilityRule, start: datetime, end: datetime, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """Concrete UTC windows of `rule` that intersect [start, end)."""
    out = []
    d = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date()
    while d <= last:
        if not rule.weekdays or d.weekday() in rule.weekdays:
            midnight = _local_midnight(d, tz)
            ws = (midnight + timedelta(minutes=rule.start_minute)).astimezone(UTC)
            we = (midnight + timedelta(minutes=rule.end_minute)).astimezone(UTC)
            if ws < end and start < we:
                out.append((ws, we))
        d += timedelta(days=1)
    return out


def overlap_minutes(rule: AvailabilityRule, start: datetime, end: datetime, tz: ZoneInfo) -> int:
    total = 0.0
    for ws, we in rule_windows(rule, start, end, tz):
        total += (min(we, end) - max(ws, start)).total_seconds() / 60
    return int(total)


def applies(rule: AvailabilityRule, kind: str) -> bool:
    return rule.applies_to is None or rule.applies_to == kind


def blocking_rules(rules: list[AvailabilityRule], start: datetime, end: datetime, kind: str, tz: ZoneInfo) -> list[AvailabilityRule]:
    return [
        r for r in rules
        if r.kind == RuleKind.BLOCK and applies(r, kind) and overlap_minutes(r, start, end, tz) > 0
    ]
