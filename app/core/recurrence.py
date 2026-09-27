"""Recurrence expansion.

Series are stored once; occurrences are computed on demand and never written
to the database. Expansion happens in local time so "every Monday 10:00" stays
at 10:00 local across DST changes.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Iterator
from zoneinfo import ZoneInfo

from .model import Commitment, Freq, Occurrence, Recurrence

UTC = timezone.utc


def _add_months(d: date, months: int, day: int) -> date | None:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    if day > calendar.monthrange(y, m)[1]:
        return None  # e.g. the 31st in a 30-day month: skipped, like RFC 5545
    return date(y, m, day)


def _candidate_dates(rule: Recurrence, anchor: date, from_date: date) -> Iterator[date]:
    """Yield candidate local dates >= anchor in ascending order.

    When the rule has no COUNT we can fast-forward to `from_date`; with a COUNT
    every occurrence from the anchor must be enumerated so it is counted.
    """
    skip_ahead = rule.count is None and from_date > anchor
    step = max(1, rule.interval)

    if rule.freq == Freq.DAILY:
        k = 0
        if skip_ahead:
            k = ((from_date - anchor).days // step) * step
        while True:
            yield anchor + timedelta(days=k)
            k += step

    elif rule.freq == Freq.WEEKLY:
        weekdays = sorted(set(rule.by_weekday)) or [anchor.weekday()]
        monday = anchor - timedelta(days=anchor.weekday())
        w = 0
        if skip_ahead:
            w = (((from_date - monday).days // 7) // step) * step
        while True:
            week_start = monday + timedelta(weeks=w)
            for wd in weekdays:
                d = week_start + timedelta(days=wd)
                if d >= anchor:
                    yield d
            w += step

    elif rule.freq == Freq.MONTHLY:
        m = 0
        if skip_ahead:
            months = (from_date.year - anchor.year) * 12 + from_date.month - anchor.month
            m = max(0, (months // step) * step)
        while True:
            d = _add_months(anchor, m, anchor.day)
            if d is not None:
                yield d
            m += step
    else:  # pragma: no cover - enum is closed
        raise ValueError(f"unsupported frequency {rule.freq}")


def expand(c: Commitment, window_start: datetime, window_end: datetime, tz: ZoneInfo) -> list[Occurrence]:
    """Occurrences of `c` whose effective interval (with buffers) touches the window."""
    pad_before = timedelta(minutes=c.buffer_before_min)
    pad_after = timedelta(minutes=c.buffer_after_min)

    if c.recurrence is None:
        occ = Occurrence(c, c.start, c.end)
        if occ.effective_start < window_end and window_start < occ.effective_end:
            return [occ]
        return []

    rule = c.recurrence
    duration = c.end - c.start
    anchor_local = c.start.astimezone(tz)
    anchor_date = anchor_local.date()
    local_time = anchor_local.time().replace(tzinfo=None)
    # Search a little before the window so long/buffered occurrences are caught.
    from_date = (window_start - duration - pad_after - timedelta(days=1)).astimezone(tz).date()

    out: list[Occurrence] = []
    produced = 0
    for d in _candidate_dates(rule, anchor_date, from_date):
        start = datetime.combine(d, local_time, tzinfo=tz).astimezone(UTC)
        if rule.until is not None and start > rule.until:
            break
        if rule.count is not None and produced >= rule.count:
            break
        produced += 1
        if start - pad_before >= window_end:
            break
        end = start + duration
        if start in c.exdates:
            continue
        if start - pad_before < window_end and window_start < end + pad_after:
            out.append(Occurrence(c, start, end))
    return out


def occurrence_starts(c: Commitment, tz: ZoneInfo, limit: int = 1000) -> list[datetime]:
    """All occurrence starts of a finite series (used for validation/UI)."""
    if c.recurrence is None:
        return [c.start]
    far = c.start + timedelta(days=3650)
    return [o.start for o in expand(c, c.start, far, tz)][:limit]
