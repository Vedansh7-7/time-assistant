"""Recurrence, buffers, availability rules, slot search, tasks (§18–§25)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.model import (
    AvailabilityRule, Commitment, Freq, Recurrence, RuleKind, SearchSettings, Status, Task,
)
from app.core.recurrence import expand
from app.core.slots import SlotRequest, find_slots
from app.core.tasks import propose_work_blocks, task_status
from app.db import repo
from app.services import queries

from .conftest import NOW, TZ, local, mk

UTC = timezone.utc


def L(day, hour, minute=0, month=9):
    return datetime(2026, month, day, hour, minute, tzinfo=TZ).astimezone(UTC)


# ---------------------------------------------------------------- recurrence
def test_weekly_recurrence_expands_in_local_time():
    c = Commitment(1, "Class", L(28, 10), L(28, 11), Status.CONFIRMED,
                   recurrence=Recurrence(Freq.WEEKLY))
    occ = expand(c, L(28, 0), L(28, 0) + timedelta(days=21), TZ)
    assert [o.start.astimezone(TZ).strftime("%a %d %H:%M") for o in occ] == \
        ["Mon 28 10:00", "Mon 05 10:00", "Mon 12 10:00"]


def test_recurrence_keeps_wall_clock_across_dst():
    ny = ZoneInfo("America/New_York")
    start = datetime(2026, 10, 26, 10, 0, tzinfo=ny).astimezone(UTC)
    c = Commitment(1, "Class", start, start + timedelta(hours=1), Status.CONFIRMED,
                   recurrence=Recurrence(Freq.WEEKLY))
    occ = expand(c, start, start + timedelta(days=15), ny)
    assert [o.start.astimezone(ny).hour for o in occ] == [10, 10, 10]  # DST ends 1 Nov


def test_recurrence_count_until_weekdays_interval_exdates():
    c = Commitment(1, "Gym", L(28, 7), L(28, 8), Status.CONFIRMED,
                   recurrence=Recurrence(Freq.WEEKLY, by_weekday=(0, 2, 4), count=4))
    assert len(expand(c, L(1, 0, month=9), L(1, 0, month=12), TZ)) == 4
    c2 = c.with_(recurrence=Recurrence(Freq.DAILY, interval=2, until=L(4, 23, month=10)))
    days = [o.start.astimezone(TZ).day for o in expand(c2, L(28, 0), L(28, 0) + timedelta(days=30), TZ)]
    assert days == [28, 30, 2, 4]
    c3 = c2.with_(exdates=frozenset({L(30, 7)}))
    assert 30 not in [o.start.astimezone(TZ).day for o in expand(c3, L(28, 0), L(28, 0) + timedelta(days=30), TZ)]


def test_monthly_skips_short_months():
    c = Commitment(1, "Rent", datetime(2026, 1, 31, 9, tzinfo=TZ).astimezone(UTC),
                   datetime(2026, 1, 31, 10, tzinfo=TZ).astimezone(UTC), Status.CONFIRMED,
                   recurrence=Recurrence(Freq.MONTHLY))
    months = [o.start.astimezone(TZ).month for o in
              expand(c, c.start, c.start + timedelta(days=100), TZ)]
    assert months == [1, 3]  # no Feb 31, no Apr 31 within window... Mar 31 yes


def test_recurring_commitment_blocks_future_occurrences(svc):
    mk(svc, "Class", local(28, 10), local(28, 11), level=4,
       recurrence={"freq": "weekly"})
    r = svc.submit("create_commitment", {"title": "Rahul", "start": local(19, 10, 30, month=10),
                                         "end": local(19, 11, 30, month=10), "authority_level": 3}, "ui")
    assert r["plan"]["outcome"] == "BLOCKED"


def test_new_recurring_series_checks_all_occurrences(svc):
    mk(svc, "Dentist", local(12, 10, month=10), local(12, 11, month=10), level=5)
    r = svc.submit("create_commitment", {"title": "Class", "start": local(28, 10), "end": local(28, 11),
                                         "recurrence": {"freq": "weekly"}}, "ui")
    assert r["plan"]["outcome"] == "BLOCKED"


def test_skip_and_move_single_occurrence(svc):
    cid = mk(svc, "Class", local(28, 10), local(28, 11), recurrence={"freq": "weekly"})
    r = svc.submit("skip_occurrence", {"id": cid, "occurrence_start": local(5, 10, month=10)}, "ui")
    if r["status"] == "needs_confirmation":
        svc.confirm(r["token"], via="button")
    week2 = svc.schedule(L(5, 0, month=10), L(6, 0, month=10))
    assert week2 == []
    r = svc.submit("move_occurrence", {"id": cid, "occurrence_start": local(12, 10, month=10),
                                       "changes": {"start": local(12, 15, month=10)}}, "ui")
    if r["status"] == "needs_confirmation":
        svc.confirm(r["token"], via="button")
    day = svc.schedule(L(12, 0, month=10), L(13, 0, month=10))
    assert [(o["title"], o["start"][11:16], o["recurring"]) for o in day] == [("Class", "15:00", False)]
    # Other weeks untouched
    assert len(svc.schedule(L(19, 0, month=10), L(20, 0, month=10))) == 1


# ---------------------------------------------------------------- buffers
def test_existing_buffer_counts(svc):
    mk(svc, "Talk", local(29, 18), local(29, 19), buffer_after_min=15)
    r = svc.submit("create_commitment", {"title": "Next", "start": local(29, 19), "end": local(29, 20),
                                         "status": "confirmed"}, "ui")
    assert r["plan"]["outcome"] == "EQUAL_CONFLICT"
    r = svc.submit("create_commitment", {"title": "Next", "start": local(29, 19, 15), "end": local(29, 20),
                                         "status": "confirmed"}, "ui")
    assert r["status"] == "executed"


def test_new_request_buffer_counts(svc):
    mk(svc, "Talk", local(29, 18), local(29, 19))
    r = svc.submit("create_commitment", {"title": "Next", "start": local(29, 19), "end": local(29, 20),
                                         "buffer_before_min": 10, "status": "confirmed"}, "ui")
    assert r["plan"]["outcome"] == "EQUAL_CONFLICT"


def test_no_invented_buffers(svc):
    cid = mk(svc, "Talk", local(29, 18), local(29, 19))
    c = repo.get_commitment(svc.conn, int(cid[1:]))
    assert (c.buffer_before_min, c.buffer_after_min) == (0, 0)


# ---------------------------------------------------------------- availability & slots
def _rules():
    return [
        AvailabilityRule(1, RuleKind.BLOCK, 19 * 60, 20 * 60, label="no meetings"),
        AvailabilityRule(2, RuleKind.PREFER, 17 * 60, 18 * 60, weekdays=(2,), label="Wed evening"),
    ]


def test_block_rule_excludes_slots_and_free_is_not_usable():
    req = SlotRequest(60, L(29, 18), L(29, 21), requested_start=L(29, 19))
    slots = find_slots(req, [], _rules(), TZ, NOW, SearchSettings(step_min=30))
    for s in slots:
        assert not (s.start < L(29, 20) and L(29, 19) < s.end), "blocked window offered"


def test_prefer_rule_ranks_first():
    req = SlotRequest(60, L(29, 8), L(1, 22, month=10))
    slots = find_slots(req, [], _rules(), TZ, NOW, SearchSettings())
    assert slots[0].start == L(30, 17)
    assert any("preferred" in r for r in slots[0].reasons)


def test_slots_skip_busy_past_and_are_distinct():
    busy = [Commitment(1, "A", L(28, 9), L(28, 12), Status.CONFIRMED)]
    req = SlotRequest(60, L(28, 0), L(28, 23))
    slots = find_slots(req, busy, [], TZ, NOW, SearchSettings(), limit=5)
    assert all(s.start >= L(28, 12) for s in slots)
    for i, a in enumerate(slots):
        for b in slots[i + 1:]:
            assert not (a.start < b.end and b.start < a.end)


def test_direct_request_into_blocked_window_warns_and_asks(svc):
    repo.upsert_rule(svc.conn, AvailabilityRule(None, RuleKind.BLOCK, 19 * 60, 20 * 60, label="no meetings"))
    r = svc.submit("create_commitment", {"title": "X", "start": local(29, 19), "end": local(29, 20)}, "ui")
    assert r["status"] == "needs_confirmation"
    assert r["plan"]["warnings"]


def test_day_view_infers_free_time(svc):
    mk(svc, "Arjun", local(29, 18), local(29, 19))
    mk(svc, "Dinner", local(29, 20), local(29, 21))
    view = queries.day(svc, datetime(2026, 9, 29).date())
    lines = view["text"].splitlines()
    assert "18:00–19:00  Arjun" in lines
    assert "19:00–20:00  Free" in lines
    assert "20:00–21:00  Dinner" in lines


# ---------------------------------------------------------------- tasks
def test_task_does_not_occupy_time_until_block_confirmed(svc):
    t = svc.create_task({"title": "ML assignment", "deadline": local(30, 23), "estimated_duration_min": 120})
    assert svc.submit("create_commitment", {"title": "X", "start": local(29, 20), "end": local(29, 22)},
                      "ui")["status"] == "executed"
    tasks = queries.tasks(svc)
    assert tasks[0]["remaining_min"] == 120
    opts = queries.work_block_options(svc, t["id"])
    assert opts["options"] and all(len(o) == 1 for o in opts["options"])
    first = opts["options"][0][0]
    r = svc.submit("create_commitment", {"title": "ML assignment", "kind": "task_block", "task_id": t["id"],
                                         "start": first["start"][:16], "end": first["end"][:16]}, "ui")
    assert r["status"] == "executed"
    assert queries.tasks(svc)[0]["remaining_min"] == 0


def test_task_at_risk_when_not_enough_free_time():
    busy = [Commitment(1, "Busy", L(28, 9), L(28, 22), Status.CONFIRMED)]
    t = Task(1, "Report", L(28, 21), 120)
    st = task_status(t, busy, [], TZ, NOW)
    assert st.at_risk and st.remaining_min == 120


def test_work_blocks_split_when_no_single_block_fits():
    busy = [Commitment(1, "A", L(28, 10), L(28, 11), Status.CONFIRMED),
            Commitment(2, "B", L(28, 12), L(28, 22), Status.CONFIRMED)]
    t = Task(1, "Report", L(28, 22), 120)
    plans = propose_work_blocks(t, 120, busy, [], TZ, NOW)
    assert len(plans) == 1
    total = sum((s.end - s.start).total_seconds() / 60 for s in plans[0])
    assert total == 120 and len(plans[0]) == 2


def test_day_view_blocks_carry_status_and_level(svc):
    mk(svc, "Arjun", local(29, 18), local(29, 19), level=4, status="tentative")
    busy = [b for b in queries.day(svc, datetime(2026, 9, 29).date())["blocks"] if b["kind"] == "busy"]
    assert busy[0]["status"] == "tentative" and busy[0]["authority_level"] == 4


def test_unsaved_candidate_has_null_id(svc):
    r = svc.check("create_commitment", {"title": "X", "start": local(29, 10), "end": local(29, 11)})
    assert r["candidate"]["id"] is None
