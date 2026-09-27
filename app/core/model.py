"""Canonical in-memory scheduling objects.

The core engine works only on these dataclasses. It never touches the database,
the network, or the wall clock (callers pass `now` explicitly), which keeps every
scheduling decision deterministic and testable.

All datetimes are timezone-aware. Storage uses UTC; local-time rules
(preferences, recurrence) are evaluated in the configured user timezone.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum

MIN_LEVEL = 1
MAX_LEVEL = 5
DEFAULT_LEVEL = 3


class Status(str, Enum):
    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NEEDS_RESCHEDULE = "needs_reschedule"


# Only these statuses occupy schedule time.
ACTIVE_STATUSES = frozenset({Status.TENTATIVE, Status.CONFIRMED})

# Allowed lifecycle transitions. Anything else is rejected by the core.
TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.TENTATIVE: frozenset({Status.CONFIRMED, Status.CANCELLED, Status.NEEDS_RESCHEDULE}),
    Status.CONFIRMED: frozenset({Status.COMPLETED, Status.CANCELLED, Status.NEEDS_RESCHEDULE}),
    Status.NEEDS_RESCHEDULE: frozenset({Status.TENTATIVE, Status.CONFIRMED, Status.CANCELLED}),
    Status.COMPLETED: frozenset(),
    Status.CANCELLED: frozenset(),
}


def can_transition(old: Status, new: Status) -> bool:
    return old == new or new in TRANSITIONS[old]


class Freq(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class Recurrence:
    """Recurrence rule, evaluated in the user's local timezone.

    The series anchor is the commitment's own start/end. `by_weekday` uses
    Monday=0 … Sunday=6 and only applies to WEEKLY rules.
    """
    freq: Freq
    interval: int = 1
    by_weekday: tuple[int, ...] = ()
    until: datetime | None = None  # inclusive, aware
    count: int | None = None


@dataclass(frozen=True)
class Commitment:
    id: int | None
    title: str
    start: datetime
    end: datetime
    status: Status = Status.TENTATIVE
    authority_level: int = DEFAULT_LEVEL
    person_ids: tuple[int, ...] = ()
    location: str | None = None
    notes: str | None = None
    buffer_before_min: int = 0
    buffer_after_min: int = 0
    recurrence: Recurrence | None = None
    # Occurrence starts (aware UTC) removed from a recurring series.
    exdates: frozenset[datetime] = frozenset()
    task_id: int | None = None
    kind: str = "meeting"  # meeting | task_block | personal
    # None = use global defaults, () = no reminders.
    reminders: tuple["ReminderSpec", ...] | None = None

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def with_(self, **kw) -> "Commitment":
        return replace(self, **kw)


@dataclass(frozen=True)
class Occurrence:
    """A concrete time block produced from a commitment (one-off or recurring)."""
    commitment: Commitment
    start: datetime
    end: datetime

    @property
    def key(self) -> str:
        """Stable machine ID: C12 for one-offs, C12@<utc-start> for occurrences."""
        base = f"C{self.commitment.id}" if self.commitment.id is not None else "C?"
        if self.commitment.recurrence is None:
            return base
        return f"{base}@{self.start.strftime('%Y%m%dT%H%MZ')}"

    @property
    def effective_start(self) -> datetime:
        return self.start - timedelta(minutes=self.commitment.buffer_before_min)

    @property
    def effective_end(self) -> datetime:
        return self.end + timedelta(minutes=self.commitment.buffer_after_min)


@dataclass(frozen=True)
class ReminderSpec:
    """Either an offset before start, or an absolute local-agnostic UTC time."""
    offset_min: int | None = None
    at: datetime | None = None
    label: str | None = None


class RuleKind(str, Enum):
    BLOCK = "block"    # hard: never suggest; direct requests get a warning
    AVOID = "avoid"    # soft penalty
    PREFER = "prefer"  # soft bonus


@dataclass(frozen=True)
class AvailabilityRule:
    """A recurring local-time window, e.g. "weekdays 19:00–20:00: do not schedule meetings".

    `weekdays` empty means every day. Minutes are minutes since local midnight;
    end_minute may be 1440. `applies_to` limits the rule to a commitment kind.
    """
    id: int | None
    kind: RuleKind
    start_minute: int
    end_minute: int
    weekdays: tuple[int, ...] = ()
    applies_to: str | None = None  # None = all kinds
    label: str = ""
    weight: int = 1


@dataclass(frozen=True)
class Task:
    id: int | None
    title: str
    deadline: datetime
    estimated_duration_min: int
    status: str = "open"  # open | done | dropped
    notes: str | None = None


@dataclass(frozen=True)
class SearchSettings:
    """Engine defaults for slot search. Configurable by the user in Preferences."""
    day_start_minute: int = 8 * 60
    day_end_minute: int = 22 * 60
    step_min: int = 15
    horizon_days: int = 14
    max_results: int = 5
    # Recurring series are checked for conflicts this far ahead.
    recurrence_check_days: int = 365


@dataclass
class ConflictItem:
    occurrence: Occurrence
    relation: str  # blocked | overridable | equal


@dataclass
class ConflictReport:
    blocked: list[ConflictItem] = field(default_factory=list)
    overridable: list[ConflictItem] = field(default_factory=list)
    equal: list[ConflictItem] = field(default_factory=list)
    rule_violations: list[AvailabilityRule] = field(default_factory=list)

    @property
    def has_time_conflict(self) -> bool:
        return bool(self.blocked or self.overridable or self.equal)

    @property
    def outcome(self) -> str:
        """FREE | BLOCKED | EQUAL_CONFLICT | OVERRIDE_POSSIBLE.

        BLOCKED wins over everything: if any existing commitment outranks the
        request, the slot is rejected even if other conflicts are overridable.
        """
        if self.blocked:
            return "BLOCKED"
        if self.equal:
            return "EQUAL_CONFLICT"
        if self.overridable:
            return "OVERRIDE_POSSIBLE"
        return "FREE"
