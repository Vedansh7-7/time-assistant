"""Overlap, authority and tentative/confirmed rules (master prompt §11–§17, user-locked semantics)."""
from app.db import repo
from app.core.model import Status

from .conftest import local, mk


def create(svc, title, start, end, level=3, status="confirmed", channel="ui", **kw):
    return svc.submit("create_commitment", {"title": title, "start": start, "end": end,
                                            "authority_level": level, "status": status, **kw}, channel)


def status_of(svc, cid):
    return repo.get_commitment(svc.conn, int(cid[1:])).status


def test_non_overlapping_and_back_to_back_are_free(svc):
    mk(svc, "Arjun", local(29, 18), local(29, 19))
    assert create(svc, "Rahul", local(29, 19), local(29, 20))["status"] == "executed"
    assert create(svc, "Early", local(29, 17), local(29, 18))["status"] == "executed"


def test_lower_authority_is_blocked_with_alternatives(svc):
    mk(svc, "Arjun", local(29, 18), local(29, 19), level=3)
    r = create(svc, "Rahul", local(29, 18, 30), local(29, 19, 30), level=2)
    assert r["status"] == "rejected"
    plan = r["plan"]
    assert plan["outcome"] == "BLOCKED"
    assert [c["title"] for c in plan["conflicts"]] == ["Arjun"]
    assert plan["alternatives"], "lower-authority conflict must produce alternatives"
    assert {o["id"] for o in plan["options"]} >= {"alternative", "change_authority"}
    # Nothing was written.
    assert len(repo.list_commitments(svc.conn)) == 1


def test_equal_authority_never_picks_a_winner(svc):
    arjun = mk(svc, "Arjun", local(29, 18), local(29, 19), level=3)
    r = create(svc, "Rahul", local(29, 18, 30), local(29, 19, 30), level=3)
    assert r["status"] == "rejected"
    assert r["plan"]["outcome"] == "EQUAL_CONFLICT"
    ids = {o["id"] for o in r["plan"]["options"]}
    assert ids == {"keep_existing", "keep_new", "alternative", "change_authority"}
    assert status_of(svc, arjun) == Status.CONFIRMED


def test_equal_authority_user_chooses_keep_new_requires_confirmation(svc):
    arjun = mk(svc, "Arjun", local(29, 18), local(29, 19), level=3)
    keep_new = next(o for o in create(svc, "Rahul", local(29, 18, 30), local(29, 19, 30))["plan"]["options"]
                    if o["id"] == "keep_new")
    r = create(svc, "Rahul", local(29, 18, 30), local(29, 19, 30), displace=keep_new["displace"])
    assert r["status"] == "needs_confirmation"
    assert status_of(svc, arjun) == Status.CONFIRMED  # not yet
    done = svc.confirm(r["token"], via="button")
    assert done["status"] == "executed"
    assert status_of(svc, arjun) == Status.NEEDS_RESCHEDULE


def test_higher_authority_asks_before_displacing(svc):
    arjun = mk(svc, "Arjun", local(29, 18), local(29, 19), level=3)
    r = create(svc, "Rahul", local(29, 18), local(29, 19), level=4)
    assert r["status"] == "needs_confirmation"
    assert r["plan"]["outcome"] == "OVERRIDE_POSSIBLE"
    assert [d["title"] for d in r["plan"]["displace"]] == ["Arjun"]
    assert status_of(svc, arjun) == Status.CONFIRMED
    svc.confirm(r["token"], via="button")
    assert status_of(svc, arjun) == Status.NEEDS_RESCHEDULE
    assert repo.displaced_by(svc.conn, int(arjun[1:])) is not None
    # Displaced items no longer occupy time and are surfaced for attention.
    assert [c["title"] for c in svc.attention()] == ["Arjun"]


def test_raising_level_of_a_request_then_override_flow(svc):
    """§16: user raises Rahul 2 -> 4; the system still asks before replacing Arjun."""
    mk(svc, "Arjun", local(29, 18), local(29, 19), level=3)
    assert create(svc, "Rahul", local(29, 18), local(29, 19), level=2)["status"] == "rejected"
    r = create(svc, "Rahul", local(29, 18), local(29, 19), level=4)
    assert r["status"] == "needs_confirmation"


def test_blocked_wins_over_overridable(svc):
    mk(svc, "Low", local(29, 18), local(29, 18, 30), level=1)
    mk(svc, "High", local(29, 18, 30), local(29, 19), level=5)
    r = create(svc, "Mid", local(29, 18), local(29, 19), level=3)
    assert r["plan"]["outcome"] == "BLOCKED"


def test_confirmed_request_can_displace_same_level_tentative_after_confirmation(svc):
    t = mk(svc, "Pencilled", local(29, 18), local(29, 19), level=3, status="tentative")
    r = create(svc, "Firm", local(29, 18), local(29, 19), level=3, status="confirmed")
    assert r["status"] == "needs_confirmation"
    assert r["plan"]["outcome"] == "OVERRIDE_POSSIBLE"
    svc.confirm(r["token"], via="button")
    assert status_of(svc, t) == Status.NEEDS_RESCHEDULE


def test_tentative_request_never_displaces_same_level_confirmed(svc):
    mk(svc, "Firm", local(29, 18), local(29, 19), level=3, status="confirmed")
    r = create(svc, "Maybe", local(29, 18), local(29, 19), level=3, status="tentative")
    assert r["plan"]["outcome"] == "BLOCKED"


def test_tentative_occupies_time(svc):
    mk(svc, "Maybe", local(29, 18), local(29, 19), level=3, status="tentative")
    r = create(svc, "Other", local(29, 18), local(29, 19), level=3, status="tentative")
    assert r["plan"]["outcome"] == "EQUAL_CONFLICT"


def test_cancelled_and_completed_do_not_occupy_time(svc):
    a = mk(svc, "Old", local(29, 18), local(29, 19), status="tentative")
    svc.submit("cancel_commitments", {"ids": [a]}, "ui")
    assert create(svc, "New", local(29, 18), local(29, 19))["status"] == "executed"


def test_force_requires_confirmation_and_displaces_everything(svc):
    a = mk(svc, "Boss", local(29, 18), local(29, 19), level=5)
    r = svc.submit("force_commitment", {"title": "Rahul", "start": local(29, 18), "end": local(29, 19)}, "ui")
    assert r["status"] == "needs_confirmation"
    assert status_of(svc, a) == Status.CONFIRMED
    assert svc.confirm(r["token"], via="button")["status"] == "executed"
    assert status_of(svc, a) == Status.NEEDS_RESCHEDULE


def test_force_without_confirmation_does_nothing(svc):
    mk(svc, "Boss", local(29, 18), local(29, 19), level=5)
    r = svc.submit("force_commitment", {"title": "Rahul", "start": local(29, 18), "end": local(29, 19)}, "ui")
    svc.reject(r["token"])
    assert [c.title for c in repo.active_commitments(svc.conn)] == ["Boss"]


def test_level_range_enforced(svc):
    import pytest
    from app.services.core import ServiceError
    for bad in (0, 6):
        with pytest.raises(ServiceError):
            create(svc, "X", local(29, 10), local(29, 11), level=bad)


def test_end_before_start_rejected(svc):
    import pytest
    from app.services.core import ServiceError
    with pytest.raises(ServiceError) as e:
        create(svc, "X", local(29, 11), local(29, 10))
    assert e.value.code == "INVALID_TIME"
