"""Operation planner: decides what a requested change would do, before doing it.

Every mutation is planned first. A plan states the outcome, the side effects
(displacements, cancellations), the confirmation required, and alternatives.
The service layer executes plans only after re-planning and checking that the
effects are unchanged, so nothing is ever applied on stale information.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import policy
from .conflicts import find_conflicts
from .model import (
    MAX_LEVEL, MIN_LEVEL, AvailabilityRule, Commitment, ConflictReport, Occurrence,
    SearchSettings, Status, can_transition,
)
from .slots import Slot, SlotRequest, find_slots


class PlanError(ValueError):
    """Invalid request (bad times, unknown transition, ...)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Plan:
    action: str
    outcome: str
    executable: bool
    confirmation: str
    message: str
    candidate: Commitment | None = None
    original: Commitment | None = None
    displace: list[Occurrence] = field(default_factory=list)
    cancel: list[Commitment] = field(default_factory=list)
    report: ConflictReport | None = None
    alternatives: list[Slot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    options: list[dict] = field(default_factory=list)
    typed_phrase: str | None = None

    def fingerprint(self) -> str:
        """Identity of the plan's effects; execution refuses if this changed."""
        c = self.candidate
        data = {
            "action": self.action,
            "outcome": self.outcome,
            "candidate": None if c is None else [c.id, c.start.isoformat(), c.end.isoformat(),
                                                 c.status.value, c.authority_level],
            "displace": sorted(o.key for o in self.displace),
            "cancel": sorted(x.id for x in self.cancel if x.id is not None),
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def validate_commitment(c: Commitment) -> None:
    if not c.title or not c.title.strip():
        raise PlanError("INVALID", "Title is required.")
    if c.end <= c.start:
        raise PlanError("INVALID_TIME", "End must be after start.")
    if c.end - c.start > timedelta(days=7):
        raise PlanError("INVALID_TIME", "A single commitment cannot be longer than 7 days.")
    if not (MIN_LEVEL <= c.authority_level <= MAX_LEVEL):
        raise PlanError("INVALID_LEVEL", f"Authority level must be {MIN_LEVEL}–{MAX_LEVEL}.")
    if c.buffer_before_min < 0 or c.buffer_after_min < 0:
        raise PlanError("INVALID", "Buffers cannot be negative.")
    if c.recurrence is not None:
        if c.recurrence.interval < 1:
            raise PlanError("INVALID_RECURRENCE", "Recurrence interval must be at least 1.")
        if c.recurrence.until is not None and c.recurrence.until < c.start:
            raise PlanError("INVALID_RECURRENCE", "Recurrence end is before the first occurrence.")
        if any(not 0 <= d <= 6 for d in c.recurrence.by_weekday):
            raise PlanError("INVALID_RECURRENCE", "Weekdays must be 0 (Mon) – 6 (Sun).")


def _alternatives(c: Commitment, existing: list[Commitment], rules: list[AvailabilityRule], tz: ZoneInfo,
                  now: datetime, settings: SearchSettings, exclude: frozenset[int] = frozenset()) -> list[Slot]:
    if c.recurrence is not None:
        return []  # recurring series are placed manually
    local_day = c.start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    req = SlotRequest(
        duration_min=int((c.end - c.start).total_seconds() // 60),
        search_start=max(now, local_day - timedelta(days=1)),
        search_end=local_day + timedelta(days=settings.horizon_days),
        kind=c.kind,
        buffer_before_min=c.buffer_before_min,
        buffer_after_min=c.buffer_after_min,
        requested_start=c.start,
        exclude_ids=exclude,
    )
    return find_slots(req, existing, rules, tz, now, settings)


def _describe(o: Occurrence) -> str:
    return o.commitment.title


def _names(occs: list[Occurrence]) -> str:
    names = [_describe(o) for o in occs]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _be(occs: list) -> str:
    return "is" if len(occs) == 1 else "are"


def _conflict_plan(action: str, candidate: Commitment, report: ConflictReport, existing, rules, tz, now,
                   settings, channel: str, original: Commitment | None = None,
                   chosen_displace: frozenset[str] = frozenset()) -> Plan:
    exclude = frozenset({candidate.id}) if candidate.id is not None else frozenset()
    outcome = report.outcome
    warnings = [f"During {r.label or 'a time you keep clear'}" for r in report.rule_violations]

    if outcome == "BLOCKED":
        alts = _alternatives(candidate, existing, rules, tz, now, settings, exclude)
        occs = [i.occurrence for i in report.blocked]
        return Plan(
            action, outcome, executable=False, confirmation=policy.NONE,
            message=f"{_names(occs)} {_be(occs)} booked then and can't be replaced by this.",
            candidate=candidate, original=original, report=report, alternatives=alts, warnings=warnings,
            options=[{"id": "alternative", "label": "Pick another time"},
                     {"id": "change_authority", "label": "Change priority"},
                     {"id": "force", "label": "Override anyway"}],
        )

    if outcome == "EQUAL_CONFLICT":
        equal_keys = {i.occurrence.key for i in report.equal}
        if not equal_keys.issubset(chosen_displace):
            alts = _alternatives(candidate, existing, rules, tz, now, settings, exclude)
            occs = [i.occurrence for i in report.equal]
            names = _names(occs)
            return Plan(
                action, outcome, executable=False, confirmation=policy.NONE,
                message=f"{names} {_be(occs)} booked then with the same priority. Which should stay?",
                candidate=candidate, original=original, report=report, alternatives=alts, warnings=warnings,
                options=[{"id": "keep_existing", "label": f"Keep {names}"},
                         {"id": "keep_new", "label": "Keep the new one", "displace": sorted(equal_keys)},
                         {"id": "alternative", "label": "Pick another time"},
                         {"id": "change_authority", "label": "Change priority"}],
            )

    # FREE, OVERRIDE_POSSIBLE, or EQUAL_CONFLICT the user explicitly resolved.
    displace = [i.occurrence for i in report.overridable + report.equal]
    alts = _alternatives(candidate, existing, rules, tz, now, settings, exclude) if displace else []
    if action == "create":
        conf = policy.for_create(channel, len(displace), len(report.rule_violations))
    else:
        assert original is not None
        conf = policy.for_update(
            channel, original.status,
            time_changed=(candidate.start, candidate.end) != (original.start, original.end),
            level_changed=candidate.authority_level != original.authority_level,
            status_changed=candidate.status != original.status,
            displaces=len(displace), rule_violations=len(report.rule_violations),
        )
    if displace:
        msg = f"This replaces {_names(displace)}, which will need a new time."
    elif warnings:
        msg = "Free, but during a time you keep clear."
    else:
        msg = "Free."
    return Plan(action, "OVERRIDE_POSSIBLE" if displace else "FREE", executable=True, confirmation=conf,
                message=msg, candidate=candidate, original=original, displace=displace, report=report,
                alternatives=alts, warnings=warnings)


def plan_create(candidate: Commitment, existing: list[Commitment], rules: list[AvailabilityRule], tz: ZoneInfo,
                now: datetime, settings: SearchSettings = SearchSettings(), channel: str = "ui",
                chosen_displace: frozenset[str] = frozenset()) -> Plan:
    validate_commitment(candidate)
    if candidate.status not in (Status.TENTATIVE, Status.CONFIRMED):
        raise PlanError("INVALID_STATUS", "New commitments must be tentative or confirmed.")
    report = find_conflicts(candidate, existing, rules, tz, settings)
    plan = _conflict_plan("create", candidate, report, existing, rules, tz, now, settings, channel,
                          chosen_displace=chosen_displace)
    if candidate.end <= now:
        plan.warnings.append("This commitment is in the past.")
    return plan


def plan_update(original: Commitment, updated: Commitment, existing: list[Commitment],
                rules: list[AvailabilityRule], tz: ZoneInfo, now: datetime,
                settings: SearchSettings = SearchSettings(), channel: str = "ui",
                chosen_displace: frozenset[str] = frozenset()) -> Plan:
    validate_commitment(updated)
    if not can_transition(original.status, updated.status):
        raise PlanError("INVALID_TRANSITION",
                        f"Cannot change status from {original.status.value} to {updated.status.value}.")
    if original.status in (Status.CANCELLED, Status.COMPLETED):
        raise PlanError("IMMUTABLE", f"A {original.status.value} commitment cannot be edited.")
    if not updated.is_active:
        # Moving to a non-occupying status (e.g. completed) needs no conflict check.
        conf = policy.for_update(channel, original.status, False, False, True, 0, 0)
        return Plan("update", "FREE", True, conf, f"Mark {updated.title} as {updated.status.value}?",
                    candidate=updated, original=original)
    report = find_conflicts(updated, existing, rules, tz, settings)
    return _conflict_plan("update", updated, report, existing, rules, tz, now, settings, channel,
                          original=original, chosen_displace=chosen_displace)


def plan_cancel(targets: list[Commitment], channel: str = "ui") -> Plan:
    live = [c for c in targets if c.status not in (Status.CANCELLED, Status.COMPLETED)]
    if not live:
        raise PlanError("NOTHING_TO_CANCEL", "No active commitments match.")
    if len(live) == 1:
        c = live[0]
        return Plan("cancel", "OK", True, policy.for_cancel(channel, c.status),
                    f"Cancel {c.title}?", original=c, cancel=live)
    phrase = policy.typed_phrase("cancel", len(live), "commitments")
    return Plan("bulk_cancel", "OK", True, policy.for_bulk(len(live)),
                f"Cancel {len(live)} commitments? Type \"{phrase}\" to confirm.",
                cancel=live, typed_phrase=phrase)


def plan_force(candidate: Commitment, existing: list[Commitment], rules: list[AvailabilityRule], tz: ZoneInfo,
               now: datetime, settings: SearchSettings = SearchSettings()) -> Plan:
    """FORCE: bypass authority. Everything overlapping is displaced. Always needs confirmation."""
    validate_commitment(candidate)
    candidate = candidate.with_(status=Status.CONFIRMED)
    report = find_conflicts(candidate, existing, rules, tz, settings)
    displace = [i.occurrence for i in report.blocked + report.equal + report.overridable]
    msg = (f"Override: {candidate.title} replaces {_names(displace)}, which will need a new time."
           if displace else f"Override: nothing is in the way of {candidate.title}.")
    return Plan("force", "FORCE", True, policy.for_force(), msg,
                candidate=candidate, displace=displace, report=report,
                warnings=[f"During {r.label or 'a time you keep clear'}" for r in report.rule_violations])
