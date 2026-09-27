"""Conflict and authority engine.

Authority rules (user-locked):
  * Authority lives on the commitment, levels 1–5.
  * new level  > existing level  -> overridable (displacement needs user confirmation)
  * new level  < existing level  -> blocked (reject + suggest alternatives)
  * equal level:
      new CONFIRMED vs existing TENTATIVE -> overridable (needs confirmation)
      new TENTATIVE vs existing CONFIRMED -> blocked
      same status                         -> equal conflict, user decides
"""
from __future__ import annotations

import bisect
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .model import (
    AvailabilityRule, Commitment, ConflictItem, ConflictReport, Occurrence,
    SearchSettings, Status,
)
from .recurrence import expand
from .rules import blocking_rules


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Half-open intervals: back-to-back blocks (6–7, 7–8) do not overlap."""
    return a_start < b_end and b_start < a_end


def relation(new_level: int, new_status: Status, existing: Commitment) -> str:
    if new_level > existing.authority_level:
        return "overridable"
    if new_level < existing.authority_level:
        return "blocked"
    if new_status == Status.CONFIRMED and existing.status == Status.TENTATIVE:
        return "overridable"
    if new_status == Status.TENTATIVE and existing.status == Status.CONFIRMED:
        return "blocked"
    return "equal"


def check_window(candidate: Commitment, settings: SearchSettings) -> tuple[datetime, datetime]:
    """The span over which a candidate's occurrences are checked."""
    start = candidate.start - timedelta(minutes=candidate.buffer_before_min)
    if candidate.recurrence is None:
        return start, candidate.end + timedelta(minutes=candidate.buffer_after_min)
    return start, candidate.start + timedelta(days=settings.recurrence_check_days)


class OccupancyIndex:
    """Active occurrences over a window, sorted for fast overlap queries."""

    def __init__(self, commitments: list[Commitment], window_start: datetime, window_end: datetime,
                 tz: ZoneInfo, ignore_ids: set[int] | frozenset[int] = frozenset()):
        occs: list[Occurrence] = []
        for c in commitments:
            if not c.is_active or (c.id is not None and c.id in ignore_ids):
                continue
            occs.extend(expand(c, window_start, window_end, tz))
        occs.sort(key=lambda o: o.effective_start)
        self.occurrences = occs
        self._starts = [o.effective_start for o in occs]
        self._max_len = max((o.effective_end - o.effective_start for o in occs), default=timedelta(0))

    def overlapping(self, start: datetime, end: datetime) -> list[Occurrence]:
        hi = bisect.bisect_left(self._starts, end)
        lo = bisect.bisect_left(self._starts, start - self._max_len)
        return [o for o in self.occurrences[lo:hi] if overlaps(start, end, o.effective_start, o.effective_end)]

    def is_free(self, start: datetime, end: datetime) -> bool:
        return not self.overlapping(start, end)


def find_conflicts(candidate: Commitment, existing: list[Commitment], rules: list[AvailabilityRule],
                   tz: ZoneInfo, settings: SearchSettings = SearchSettings(),
                   ignore_ids: set[int] | frozenset[int] = frozenset()) -> ConflictReport:
    """Classify every overlap between `candidate` and the active schedule."""
    ws, we = check_window(candidate, settings)
    ignore = set(ignore_ids)
    if candidate.id is not None:
        ignore.add(candidate.id)
    index = OccupancyIndex(existing, ws, we, tz, ignore)

    report = ConflictReport()
    seen: set[str] = set()
    seen_rules: set[int | None] = set()
    for occ in expand(candidate, ws, we, tz):
        for other in index.overlapping(occ.effective_start, occ.effective_end):
            if other.key in seen:
                continue
            seen.add(other.key)
            rel = relation(candidate.authority_level, candidate.status, other.commitment)
            getattr(report, rel).append(ConflictItem(other, rel))
        for rule in blocking_rules(rules, occ.start, occ.end, candidate.kind, tz):
            key = rule.id if rule.id is not None else id(rule)
            if key not in seen_rules:
                seen_rules.add(key)
                report.rule_violations.append(rule)
    return report
