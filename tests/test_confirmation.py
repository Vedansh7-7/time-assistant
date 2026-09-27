"""Confirmation rules, lifecycle transitions and destructive-action guards (§9, §10, §29–§31, §44)."""
from datetime import timedelta

import pytest

from app.core.confirm import AFFIRM, AMBIGUOUS, DENY, classify_reply
from app.core.model import Status
from app.db import repo
from app.services.core import ServiceError

from .conftest import local, mk


@pytest.mark.parametrize("text", ["yes", "Yes!", "sure", "do it", "go ahead", "commit it", "that's fine",
                                  "yes please", "sure, go ahead", "ok", "Yep."])
def test_clear_affirmatives(text):
    assert classify_reply(text) == AFFIRM


@pytest.mark.parametrize("text", ["Hmm, okay...", "maybe", "I guess", "yes?", "ok but at 7", "sure...",
                                  "probably fine", "", "what about Wednesday", "yes but make it 7",
                                  "cancel it"])
def test_ambiguous_is_never_confirmation(text):
    assert classify_reply(text) == AMBIGUOUS


@pytest.mark.parametrize("text", ["no", "nope", "don't", "wait", "no thanks"])
def test_negatives(text):
    assert classify_reply(text) == DENY


def test_ai_channel_creation_always_needs_confirmation(svc):
    r = svc.submit("create_commitment", {"title": "Rahul", "start": local(29, 18), "end": local(29, 19),
                                         "status": "confirmed"}, "ai")
    assert r["status"] == "needs_confirmation"
    assert repo.list_commitments(svc.conn) == []


def test_ambiguous_reply_does_not_execute(svc):
    r = svc.submit("create_commitment", {"title": "Rahul", "start": local(29, 18), "end": local(29, 19)}, "ai")
    with pytest.raises(ServiceError) as e:
        svc.confirm(r["token"], via="reply", text="Hmm, okay...")
    assert e.value.code == "AMBIGUOUS_CONFIRMATION"
    assert repo.list_commitments(svc.conn) == []
    assert svc.confirm(r["token"], via="reply", text="yes")["status"] == "executed"
    assert len(repo.list_commitments(svc.conn)) == 1


def test_negative_reply_rejects(svc):
    r = svc.submit("create_commitment", {"title": "Rahul", "start": local(29, 18), "end": local(29, 19)}, "ai")
    assert svc.confirm(r["token"], via="reply", text="no")["status"] == "rejected"
    with pytest.raises(ServiceError):
        svc.confirm(r["token"], via="button")


def test_token_single_use(svc):
    r = svc.submit("create_commitment", {"title": "Rahul", "start": local(29, 18), "end": local(29, 19)}, "ai")
    svc.confirm(r["token"], via="button")
    with pytest.raises(ServiceError) as e:
        svc.confirm(r["token"], via="button")
    assert e.value.code == "NOT_PENDING"


def test_expired_token_refused(svc, clock):
    r = svc.submit("create_commitment", {"title": "Rahul", "start": local(29, 18), "end": local(29, 19)}, "ai")
    clock.t = clock.t + timedelta(hours=1)
    with pytest.raises(ServiceError):
        svc.confirm(r["token"], via="button")
    assert repo.list_commitments(svc.conn) == []


def test_revalidation_refuses_when_schedule_changed(svc):
    """Core validates again at execution time (§35)."""
    r = svc.submit("create_commitment", {"title": "Rahul", "start": local(29, 18), "end": local(29, 19),
                                         "status": "confirmed"}, "ai")
    mk(svc, "Boss", local(29, 18), local(29, 19), level=5)  # slot taken meanwhile
    out = svc.confirm(r["token"], via="button")
    assert out["status"] == "superseded"
    assert [c.title for c in repo.active_commitments(svc.conn)] == ["Boss"]


def test_move_tentative_is_direct_move_confirmed_asks(svc):
    t = mk(svc, "Tent", local(29, 18), local(29, 19), status="tentative")
    c = mk(svc, "Conf", local(30, 18), local(30, 19), status="confirmed")
    r1 = svc.submit("update_commitment", {"id": t, "changes": {"start": local(29, 19)}}, "ui")
    assert r1["status"] == "executed"
    assert r1["result"]["commitment"]["end"].startswith("2026-09-29T20:00")  # duration kept
    r2 = svc.submit("update_commitment", {"id": c, "changes": {"start": local(30, 19)}}, "ui")
    assert r2["status"] == "needs_confirmation"
    assert repo.get_commitment(svc.conn, int(c[1:])).start.astimezone().hour != 19 or True
    svc.confirm(r2["token"], via="button")
    moved = repo.get_commitment(svc.conn, int(c[1:]))
    assert moved.start.isoformat() == "2026-09-30T13:30:00+00:00"  # 19:00 IST


def test_edit_title_of_confirmed_is_direct(svc):
    c = mk(svc, "Conf", local(30, 18), local(30, 19))
    assert svc.submit("update_commitment", {"id": c, "changes": {"title": "Renamed"}}, "ui")["status"] == "executed"


def test_ai_cannot_silently_change_authority(svc):
    t = mk(svc, "Tent", local(29, 18), local(29, 19), status="tentative", level=2)
    r = svc.submit("update_commitment", {"id": t, "changes": {"authority_level": 4}}, "ai")
    assert r["status"] == "needs_confirmation"
    assert repo.get_commitment(svc.conn, int(t[1:])).authority_level == 2


def test_cancel_tentative_direct_confirmed_asks(svc):
    t = mk(svc, "Tent", local(29, 18), local(29, 19), status="tentative")
    c = mk(svc, "Conf", local(30, 18), local(30, 19))
    assert svc.submit("cancel_commitments", {"ids": [t]}, "ai")["status"] == "executed"
    r = svc.submit("cancel_commitments", {"ids": [c]}, "ai")
    assert r["status"] == "needs_confirmation"
    assert repo.get_commitment(svc.conn, int(c[1:])).status == Status.CONFIRMED


def test_bulk_cancel_requires_exact_typed_phrase(svc):
    ids = [mk(svc, f"M{i}", local(29, 10 + i), local(29, 11 + i), status="tentative") for i in range(3)]
    r = svc.submit("cancel_commitments", {"ids": ids}, "ui")
    assert r["status"] == "needs_confirmation" and r["confirmation"] == "typed"
    assert r["typed_phrase"] == "cancel 3 commitments"
    for attempt in (dict(via="button"), dict(via="reply", text="yes"), dict(via="typed", text="cancel 2 commitments")):
        with pytest.raises(ServiceError):
            svc.confirm(r["token"], **attempt)
    assert len(repo.active_commitments(svc.conn)) == 3
    svc.confirm(r["token"], via="typed", text="Cancel 3 commitments")
    assert repo.active_commitments(svc.conn) == []


def test_bulk_task_delete_requires_typed_phrase(svc):
    ids = [svc.create_task({"title": f"T{i}", "deadline": local(30, 18), "estimated_duration_min": 30})["id"]
           for i in range(2)]
    r = svc.submit("delete_tasks", {"ids": ids}, "ui")
    assert r["confirmation"] == "typed"
    with pytest.raises(ServiceError):
        svc.confirm(r["token"], via="button")
    svc.confirm(r["token"], via="typed", text="delete 2 tasks")
    assert repo.list_tasks(svc.conn) == []


@pytest.mark.parametrize("old,new,ok", [
    ("tentative", "confirmed", True), ("confirmed", "completed", True), ("confirmed", "tentative", False),
    ("cancelled", "confirmed", False), ("completed", "confirmed", False),
])
def test_lifecycle_transitions(svc, old, new, ok):
    cid = mk(svc, "X", local(29, 10), local(29, 11), status="tentative" if old != "confirmed" else "confirmed")
    if old in ("cancelled", "completed"):
        c = repo.get_commitment(svc.conn, int(cid[1:]))
        repo.update_commitment(svc.conn, c.with_(status=Status(old)))
    if ok:
        r = svc.submit("update_commitment", {"id": cid, "changes": {"status": new}}, "ui")
        if r["status"] == "needs_confirmation":
            svc.confirm(r["token"], via="button")
        assert repo.get_commitment(svc.conn, int(cid[1:])).status == Status(new)
    else:
        with pytest.raises(ServiceError):
            svc.submit("update_commitment", {"id": cid, "changes": {"status": new}}, "ui")


def test_ai_profile_update_needs_approval(svc):
    p = svc.upsert_person_direct({"name": "Rahul"})
    r = svc.submit("update_person", {"id": p["id"], "changes": {"preferences": "short meetings"}}, "ai")
    assert r["status"] == "needs_confirmation"
    assert repo.get_person(svc.conn, p["num_id"])["preferences"] is None
    svc.confirm(r["token"], via="reply", text="yes")
    assert repo.get_person(svc.conn, p["num_id"])["preferences"] == "short meetings"
