"""Public booking channel."""
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.core.model import Status
from app.db import repo
from app.db.connection import connect, migrate
from app.main import create_app
from app.public_app import create_public_app
from app.services import mailer, public
from app.services.core import ServiceError

from .conftest import NOW, Clock, local, mk

PUBLIC_CFG = {"enabled": True, "owner_name": "Asha", "weekdays": [0, 1, 2, 3, 4], "day_start": "10:00",
              "day_end": "19:00", "min_notice_hours": 12, "days_ahead": 14}


@pytest.fixture
def pub(svc):
    repo.set_setting(svc.conn, "public", PUBLIC_CFG)
    return svc


def req(svc, start=local(29, 11), **kw):
    body = {"name": "Visitor", "email": "v@example.com", "motive": "Coffee chat", "duration_min": 30,
            "start": start, **kw}
    return public.create_request(svc, body, "203.0.113.5")


def test_disabled_by_default(svc):
    with pytest.raises(ServiceError) as e:
        public.suggest(svc, 30)
    assert e.value.code == "PUBLIC_DISABLED"


def test_visitors_see_reserved_not_titles(pub):
    mk(pub, "Secret therapy session", local(29, 11), local(29, 12), notes="private")
    day = public.public_day(pub, NOW.date().replace(day=29), 30)
    blob = repr(day)
    assert "Secret" not in blob and "private" not in blob
    assert day["reserved"] == [{"start": "2026-09-29T11:00:00+05:30", "end": "2026-09-29T12:00:00+05:30",
                                "label": "Reserved", "text": "11:00–12:00"}]
    times = [s["time"] for s in day["slots"]]
    assert "11:00" not in times and "11:30" not in times and "12:00" in times and "10:30" in times


def test_suggestions_respect_notice_hours_and_weekdays(pub):
    out = public.suggest(pub, 30)
    days = [d["date"] for d in out["days"]]
    assert "2026-09-28" not in days            # 12h notice from Mon 09:00 -> earliest Mon 21:00 (after hours)
    assert "2026-10-03" not in days and "2026-10-04" not in days  # weekend closed
    assert len({s["start"][:10] for s in out["suggestions"]}) == len(out["suggestions"])  # spread across days
    assert all("reasons" not in s for s in out["suggestions"])


def test_request_must_be_an_offered_slot(pub):
    with pytest.raises(ServiceError):
        req(pub, start=local(29, 8))  # before public hours
    with pytest.raises(ServiceError):
        req(pub, duration_min=37)
    mk(pub, "Busy", local(29, 11), local(29, 12))
    with pytest.raises(ServiceError) as e:
        req(pub)
    assert e.value.code == "SLOT_TAKEN"


def test_request_does_not_occupy_time_and_queues_email(pub):
    r = req(pub)
    assert r["status"] == "requested"
    assert repo.active_commitments(pub.conn) == []
    assert pub.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    assert public.request_status(pub, r["ref"])["status"] == "requested"


def test_honeypot_and_rate_limits(pub):
    with pytest.raises(ServiceError):
        req(pub, website="http://spam")
    for h in (11, 12, 13):
        req(pub, start=local(29, h))
    with pytest.raises(ServiceError) as e:
        req(pub, start=local(29, 14))  # 4th open request for the same email
    assert e.value.code == "RATE_LIMITED"


def test_confirm_creates_level1_confirmed_and_emails(pub):
    r = req(pub)
    rid = pub.conn.execute("SELECT id FROM public_requests").fetchone()[0]
    out = public.decide(pub, rid, "confirm")
    assert out["status"] == "confirmed"
    (c,) = repo.active_commitments(pub.conn)
    assert c.authority_level == 1 and c.status == Status.CONFIRMED
    subjects = [x[0] for x in pub.conn.execute("SELECT subject FROM outbox ORDER BY id")]
    assert subjects[-1] == "Your meeting is confirmed"
    assert public.request_status(pub, r["ref"])["status"] == "confirmed"


def test_waitlist_then_confirm(pub):
    req(pub)
    public.decide(pub, 1, "waitlist")
    (c,) = repo.active_commitments(pub.conn)
    assert c.status == Status.TENTATIVE and c.authority_level == 1
    public.decide(pub, 1, "confirm")
    assert repo.active_commitments(pub.conn)[0].status == Status.CONFIRMED


def test_decline_with_shared_reason_and_waitlist_release(pub):
    r = req(pub)
    public.decide(pub, 1, "waitlist")
    public.decide(pub, 1, "decline", reason="Travelling that week", share_reason=True)
    assert repo.active_commitments(pub.conn) == []
    st = public.request_status(pub, r["ref"])
    assert st["status"] == "declined" and st["reason"] == "Travelling that week"
    html = pub.conn.execute("SELECT html FROM outbox ORDER BY id DESC").fetchone()[0]
    assert "Travelling that week" in html


def test_decline_reason_private_by_default(pub):
    r = req(pub)
    public.decide(pub, 1, "decline", reason="not interested")
    assert public.request_status(pub, r["ref"])["reason"] is None
    assert "not interested" not in pub.conn.execute("SELECT html FROM outbox ORDER BY id DESC").fetchone()[0]


def test_public_booking_never_displaces_anything(pub):
    req(pub)
    mk(pub, "Pencilled", local(29, 11), local(29, 11, 30), level=1, status="tentative")
    out = public.decide(pub, 1, "confirm")
    assert out["status"] == "not_possible"
    assert [c.title for c in repo.active_commitments(pub.conn)] == ["Pencilled"]
    assert pub.pending() == []  # no dangling confirmation token


def test_withdraw_releases_waitlisted_time(pub):
    r = req(pub)
    public.decide(pub, 1, "waitlist")
    public.withdraw(pub, r["ref"])
    assert repo.active_commitments(pub.conn) == []


def test_email_template_escapes_user_input(pub):
    req(pub, name="<script>x</script>", motive="<b>hi</b>")
    html = pub.conn.execute("SELECT html FROM outbox").fetchone()[0]
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_public_app_exposes_no_owner_routes(tmp_path):
    cfg = Config(tmp_path, tmp_path / "p.db", tmp_path / "b", run_background=False)
    conn = connect(cfg.db_path)
    migrate(conn)
    repo.set_setting(conn, "public", PUBLIC_CFG)
    repo.set_setting(conn, "timezone", "Asia/Kolkata")
    conn.close()
    c = TestClient(create_public_app(cfg, Clock(NOW)))
    for path in ("/api/dashboard", "/api/commitments", "/api/people", "/api/settings", "/api/requests",
                 "/api/system", "/mcp", "/docs", "/openapi.json"):
        assert c.get(path).status_code in (404, 405), path
    assert c.get("/api/public/profile").json()["owner_name"] == "Asha"
    r = c.post("/api/public/requests", json={"name": "V", "email": "v@example.com", "motive": "Hello there",
                                             "duration_min": 30, "start": local(29, 11)})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/public/requests/{r.json()['ref']}").json()["status"] == "requested"


def test_suggestions_vary_time_of_day(pub):
    hours = [s["time"][:2] for s in public.suggest(pub, 30)["suggestions"]]
    assert len(hours) == 5 and len(set(hours)) == 5


def test_day_without_duration_is_structured_error(pub):
    with pytest.raises(ServiceError) as e:
        public.public_day(pub, NOW.date().replace(day=29), 0)
    assert e.value.code == "INVALID_DURATION"
