"""Task engine. Tasks never occupy time; only confirmed work blocks (commitments) do."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .model import ACTIVE_STATUSES, AvailabilityRule, Commitment, SearchSettings, Status, Task
from .slots import Slot, SlotRequest, find_slots

MIN_CHUNK_MIN = 30


@dataclass
class TaskStatus:
    task: Task
    scheduled_min: int
    completed_min: int
    remaining_min: int
    free_min_before_deadline: int
    at_risk: bool
    overdue: bool


def scheduled_minutes(task: Task, commitments: list[Commitment]) -> tuple[int, int]:
    """(minutes in active future-or-current blocks, minutes in completed blocks)."""
    active = done = 0
    for c in commitments:
        if c.task_id != task.id:
            continue
        mins = int((c.end - c.start).total_seconds() // 60)
        if c.status in ACTIVE_STATUSES:
            active += mins
        elif c.status == Status.COMPLETED:
            done += mins
    return active, done


def task_status(task: Task, commitments: list[Commitment], rules: list[AvailabilityRule], tz: ZoneInfo,
                now: datetime, settings: SearchSettings = SearchSettings()) -> TaskStatus:
    active, done = scheduled_minutes(task, commitments)
    remaining = max(0, task.estimated_duration_min - active - done)
    free = 0
    if remaining and task.status == "open" and task.deadline > now:
        # Sum usable free time before the deadline, stepping in chunk-sized blocks.
        req = SlotRequest(duration_min=MIN_CHUNK_MIN, search_start=now, search_end=task.deadline, kind="task_block")
        free = len(find_slots(req, commitments, rules, tz, now, settings, limit=10_000)) * MIN_CHUNK_MIN
    overdue = task.status == "open" and task.deadline <= now and remaining > 0
    return TaskStatus(
        task=task,
        scheduled_min=active,
        completed_min=done,
        remaining_min=remaining,
        free_min_before_deadline=free,
        at_risk=task.status == "open" and remaining > 0 and (overdue or free < remaining),
        overdue=overdue,
    )


def propose_work_blocks(task: Task, remaining_min: int, commitments: list[Commitment],
                        rules: list[AvailabilityRule], tz: ZoneInfo, now: datetime,
                        settings: SearchSettings = SearchSettings(), max_options: int = 3) -> list[list[Slot]]:
    """Candidate plans for the remaining work. Each plan is a list of blocks.

    Prefer a single contiguous block; otherwise split into the fewest chunks
    that fit before the deadline. Nothing is scheduled here: plans are proposals.
    """
    if remaining_min <= 0 or task.deadline <= now:
        return []
    single = find_slots(
        SlotRequest(remaining_min, now, task.deadline, kind="task_block"),
        commitments, rules, tz, now, settings, limit=max_options,
    )
    if single:
        return [[s] for s in single]

    # Greedy split: repeatedly take the best largest block that still fits.
    plan: list[Slot] = []
    placed: list[Commitment] = list(commitments)
    left = remaining_min
    chunk = remaining_min
    while left > 0 and chunk >= MIN_CHUNK_MIN:
        size = min(chunk, left)
        found = find_slots(SlotRequest(size, now, task.deadline, kind="task_block"),
                           placed, rules, tz, now, settings, limit=1)
        if not found:
            chunk -= MIN_CHUNK_MIN
            continue
        s = found[0]
        plan.append(s)
        placed.append(Commitment(None, "(planned)", s.start, s.end, Status.CONFIRMED, 5))
        left -= size
    if left > 0:
        return []  # cannot fit before the deadline; caller reports at-risk
    plan.sort(key=lambda s: s.start)
    return [plan]
