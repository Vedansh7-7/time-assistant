"""Public booking channel.

Visitors never see commitment details. They see open start times and, where
the owner is busy, a neutral label ("Reserved"). A request does not occupy
time until the owner acts on it:

    requested --confirm--> confirmed   (level-1 confirmed commitment)
    requested --waitlist-> waitlisted  (level-1 tentative commitment)
    waitlisted --confirm-> confirmed
    requested|waitlisted --decline--> declined (optional reason shared)
    requested|waitlisted --withdraw (by requester)--> withdrawn

Every write to the schedule goes through CoreService, so authority and
conflict rules apply exactly as for the owner's own actions.
"""
from __future__ import annotations

import hashlib
import html
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from ..core.model import AvailabilityRule, RuleKind, SearchSettings, Status
from ..core.slots import SlotRequest, valid_slots
from ..db import repo
from ..db.connection import utcnow, write_tx
from . import mailer
from . import serialize as ser
from . import settings as settings_mod
from .core import CoreService, ServiceError

UTC = timezone.utc
PUBLIC_LEVEL = 1  # user-locked: public bookings are always authority level 1
EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[^@\s<>\"']{1,190}\.[A-Za-z]{2,24}$")
OPEN = ("requested", "waitlisted")


def _cfg(svc: CoreService) -> dict:
    return settings_mod.get(svc.conn, "public")


def _require_enabled(svc: CoreService) -> dict:
    cfg = _cfg(svc)
    if not cfg["enabled"]:
        raise ServiceError("PUBLIC_DISABLED", "Booking is not open right now.", 503)
    return cfg


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _search(cfg: dict) -> SearchSettings:
    return SearchSettings(day_start_minute=_minutes(cfg["day_start"]), day_end_minute=_minutes(cfg["day_end"]),
                          step_min=int(cfg["step_min"]), horizon_days=int(cfg["days_ahead"]))


def _rules(svc: CoreService, cfg: dict) -> list[AvailabilityRule]:
    """Owner's rules plus an all-day block on non-public weekdays."""
    rules = repo.list_rules(svc.conn)
    closed = tuple(d for d in range(7) if d not in set(int(x) for x in cfg["weekdays"]))
    if closed:
        rules.append(AvailabilityRule(-1, RuleKind.BLOCK, 0, 1440, closed, None, "closed"))
    return rules


def _window(svc: CoreService, cfg: dict) -> tuple[datetime, datetime]:
    now = svc.now()
    start = now + timedelta(hours=float(cfg["min_notice_hours"]))
    local_today = now.astimezone(svc.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = local_today + timedelta(days=int(cfg["days_ahead"]) + 1)
    return start, end.astimezone(UTC)


def _slots(svc: CoreService, cfg: dict, duration_min: int, start=None, end=None):
    if int(duration_min) not in [int(d) for d in cfg["durations_min"]]:
        raise ServiceError("INVALID_DURATION", "Please pick one of the offered durations.")
    ws, we = _window(svc, cfg)
    s, e = max(ws, start or ws), min(we, end or we)
    if s >= e:
        return []
    req = SlotRequest(int(duration_min), s, e, kind="meeting")
    return valid_slots(req, repo.active_commitments(svc.conn), _rules(svc, cfg), svc.tz, svc.now(), _search(cfg))


# ------------------------------------------------------------------ public reads
def profile(svc: CoreService) -> dict:
    cfg = _cfg(svc)
    ws, we = _window(svc, cfg)
    return {"enabled": bool(cfg["enabled"]), "owner_name": cfg["owner_name"], "headline": cfg["headline"],
            "intro": cfg["intro"], "durations_min": cfg["durations_min"], "timezone": str(svc.tz),
            "busy_label": cfg["busy_label"], "today": svc.now().astimezone(svc.tz).date().isoformat(),
            "first_day": ws.astimezone(svc.tz).date().isoformat(),
            "last_day": (we - timedelta(seconds=1)).astimezone(svc.tz).date().isoformat()}


def suggest(svc: CoreService, duration_min: int, limit: int = 5) -> dict:
    """Ranked suggestions spread across days, plus which days have any opening."""
    cfg = _require_enabled(svc)
    slots = _slots(svc, cfg, duration_min)
    days: dict[str, list] = {}
    for s in slots:
        days.setdefault(s.start.astimezone(svc.tz).date().isoformat(), []).append(s)
    # One suggestion per day, best-scored days first; vary the time of day so the
    # visitor sees morning/afternoon/evening options, not 10:00 five times.
    order = sorted(days, key=lambda d: min((s.score, s.start) for s in days[d]))
    ranked, used_times = [], set()
    for d in order[:limit]:
        options = sorted(days[d], key=lambda s: (s.score, s.start))
        best = options[0].score
        pick = next((s for s in options if s.score <= best + 30
                     and s.start.astimezone(svc.tz).strftime("%H") not in used_times), options[0])
        used_times.add(pick.start.astimezone(svc.tz).strftime("%H"))
        ranked.append(pick)
    ranked.sort(key=lambda s: s.start)
    return {"suggestions": [_public_slot(s, svc) for s in ranked],
            "days": [{"date": d, "open_slots": len(v)} for d, v in sorted(days.items())]}


def _public_slot(s, svc: CoreService) -> dict:
    # Reasons can leak rule labels, so they are never sent publicly.
    return {"start": ser.local_iso(s.start, svc.tz), "end": ser.local_iso(s.end, svc.tz),
            "when": ser.human_span(s.start, s.end, svc.tz), "time": ser.human_time(s.start, svc.tz)}


def public_day(svc: CoreService, d: date, duration_min: int) -> dict:
    """Open start times and anonymous 'Reserved' blocks for one day."""
    cfg = _require_enabled(svc)
    day_start = datetime(d.year, d.month, d.day, tzinfo=svc.tz)
    s = (day_start + timedelta(minutes=_minutes(cfg["day_start"]))).astimezone(UTC)
    e = (day_start + timedelta(minutes=_minutes(cfg["day_end"]))).astimezone(UTC)
    slots = _slots(svc, cfg, duration_min, s, e)
    reserved = []
    for o in svc.schedule(s, e):
        rs = max(datetime.fromisoformat(o["start"]), s)
        re_ = min(datetime.fromisoformat(o["end"]), e)
        if reserved and datetime.fromisoformat(reserved[-1]["end"]) >= rs:  # merge adjacent busy blocks
            reserved[-1]["end"] = ser.local_iso(max(re_, datetime.fromisoformat(reserved[-1]["end"])), svc.tz)
            continue
        reserved.append({"start": ser.local_iso(rs, svc.tz), "end": ser.local_iso(re_, svc.tz),
                         "label": cfg["busy_label"]})
    for r in reserved:
        r["text"] = f"{ser.human_time(datetime.fromisoformat(r['start']), svc.tz)}–" \
                    f"{ser.human_time(datetime.fromisoformat(r['end']), svc.tz)}"
    return {"date": d.isoformat(), "slots": [_public_slot(x, svc) for x in slots], "reserved": reserved,
            "day_start": cfg["day_start"], "day_end": cfg["day_end"]}


# ------------------------------------------------------------------ public writes
def _client_hash(svc: CoreService, ip: str) -> str:
    salt = repo.get_setting(svc.conn, "_salt")
    if not salt:
        salt = secrets.token_hex(16)
        with write_tx(svc.conn):
            repo.set_setting(svc.conn, "_salt", salt)
    return hashlib.sha256((salt + (ip or "")).encode()).hexdigest()[:32]


def _row(svc: CoreService, ref: str) -> dict:
    r = svc.conn.execute("SELECT * FROM public_requests WHERE ref = ?", (ref,)).fetchone()
    if r is None:
        raise ServiceError("NOT_FOUND", "Request not found", 404)
    return dict(r)


def _urls(cfg: dict, ref: str) -> tuple[str, str]:
    base = (cfg.get("public_base_url") or "").rstrip("/")
    return f"{base}/book/status/{ref}", f"{base}/book"


def _mail_values(svc: CoreService, cfg: dict, row: dict) -> dict:
    s, e = repo.parse(row["start"]), repo.parse(row["end"])
    status_url, book_url = _urls(cfg, row["ref"])
    return {"name": row["name"], "email": row["email"], "motive": row["motive"],
            "when": ser.human_span(s, e, svc.tz) + f" ({svc.tz})", "duration": row["duration_min"],
            "owner_name": cfg["owner_name"] or "me", "status_url": status_url, "book_url": book_url}


def create_request(svc: CoreService, body: dict, ip: str) -> dict:
    cfg = _require_enabled(svc)
    if body.get("website"):  # honeypot field, invisible to humans
        raise ServiceError("REJECTED", "Request rejected.", 400)
    name = str(body.get("name") or "").strip()
    email = str(body.get("email") or "").strip().lower()
    motive = str(body.get("motive") or "").strip()
    if not 1 <= len(name) <= 80:
        raise ServiceError("INVALID", "Please enter your name (max 80 characters).")
    if not EMAIL_RE.match(email):
        raise ServiceError("INVALID", "Please enter a valid email address.")
    if not 3 <= len(motive) <= 500:
        raise ServiceError("INVALID", "Please describe the purpose (3–500 characters).")
    duration = int(body.get("duration_min") or 0)
    start = svc._dt(body.get("start"))
    if not any(s.start == start for s in _slots(svc, cfg, duration, start, start + timedelta(minutes=duration))):
        raise ServiceError("SLOT_TAKEN", "That time is no longer available. Please pick another.", 409)

    client = _client_hash(svc, ip)
    since = (svc.now() - timedelta(days=1)).isoformat()
    per_ip = svc.conn.execute("SELECT COUNT(*) FROM public_requests WHERE client_hash = ? AND created_at > ?",
                              (client, since)).fetchone()[0]
    if per_ip >= int(cfg["max_requests_per_ip_per_day"]):
        raise ServiceError("RATE_LIMITED", "Too many requests today. Please try again tomorrow.", 429)
    open_for_email = svc.conn.execute(
        f"SELECT COUNT(*) FROM public_requests WHERE email = ? AND status IN ({','.join('?' * len(OPEN))})",
        (email, *OPEN)).fetchone()[0]
    if open_for_email >= int(cfg["max_open_per_email"]):
        raise ServiceError("RATE_LIMITED", "You already have several open requests.", 429)

    ref = secrets.token_urlsafe(16)
    now = svc.now().isoformat()
    end = start + timedelta(minutes=duration)
    with write_tx(svc.conn):
        svc.conn.execute(
            "INSERT INTO public_requests (ref, name, email, motive, duration_min, start, end, client_hash, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ref, name, email, motive, duration, repo.iso(start), repo.iso(end), client, now, now))
        row = _row(svc, ref)
        values = _mail_values(svc, cfg, row)
        mailer.queue(svc.conn, email, "Your meeting request was received", "request_received", **values)
        owner_addr = settings_mod.get(svc.conn, "email").get("username")
        if owner_addr:
            mailer.queue(svc.conn, owner_addr, f"New request: {name}", "owner_new_request", **values)
    return {"ref": ref, "status": "requested", "when": values["when"]}


def request_status(svc: CoreService, ref: str) -> dict:
    row = _row(svc, ref)
    s, e = repo.parse(row["start"]), repo.parse(row["end"])
    return {"status": row["status"], "name": row["name"], "motive": row["motive"],
            "duration_min": row["duration_min"], "when": ser.human_span(s, e, svc.tz), "timezone": str(svc.tz),
            "reason": row["reason"] if row["share_reason"] and row["status"] == "declined" else None,
            "can_withdraw": row["status"] in OPEN}


def withdraw(svc: CoreService, ref: str) -> dict:
    row = _row(svc, ref)
    if row["status"] not in OPEN:
        raise ServiceError("NOT_OPEN", f"This request is already {row['status']}.", 409)
    if row["commitment_id"]:
        c = repo.get_commitment(svc.conn, row["commitment_id"])
        if c and c.is_active:
            with write_tx(svc.conn):
                repo.update_commitment(svc.conn, c.with_(status=Status.CANCELLED))
    _set_status(svc, row["id"], "withdrawn")
    return request_status(svc, ref)


# ------------------------------------------------------------------ owner side
def _set_status(svc: CoreService, rid: int, status: str, **extra) -> None:
    cols = ", ".join(f"{k} = ?" for k in extra)
    with write_tx(svc.conn):
        svc.conn.execute(f"UPDATE public_requests SET status = ?, updated_at = ?{', ' + cols if cols else ''} "
                         f"WHERE id = ?", (status, utcnow().isoformat(), *extra.values(), rid))


def owner_list(svc: CoreService, statuses: list[str] | None = None) -> list[dict]:
    q = "SELECT * FROM public_requests"
    args: list = []
    if statuses:
        q += f" WHERE status IN ({','.join('?' * len(statuses))})"
        args = statuses
    out = []
    for r in svc.conn.execute(q + " ORDER BY start", args):
        d = dict(r)
        s, e = repo.parse(d["start"]), repo.parse(d["end"])
        d.pop("client_hash", None)
        d.pop("ref", None)
        d.update(id=f"Q{d['id']}", num_id=d["id"], start=ser.local_iso(s, svc.tz), end=ser.local_iso(e, svc.tz),
                 when=ser.human_span(s, e, svc.tz),
                 commitment_id=f"C{d['commitment_id']}" if d["commitment_id"] else None,
                 check=svc.check("create_commitment", _commitment_payload(d, s, e, "confirmed"), "ui")
                 if d["status"] == "requested" else None)
        out.append(d)
    return out


def _commitment_payload(row: dict, s: datetime, e: datetime, status: str) -> dict:
    return {"title": f"{row['name']}: {row['motive'][:60]}", "start": s.isoformat(), "end": e.isoformat(),
            "status": status, "authority_level": PUBLIC_LEVEL, "kind": "meeting",
            "notes": f"Public request from {row['name']} <{row['email']}>:\n{row['motive']}"}


def decide(svc: CoreService, rid, decision: str, reason: str | None = None, share_reason: bool = False) -> dict:
    num = int(str(rid).lstrip("Qq"))
    r = svc.conn.execute("SELECT * FROM public_requests WHERE id = ?", (num,)).fetchone()
    if r is None:
        raise ServiceError("NOT_FOUND", "Request not found", 404)
    row = dict(r)
    cfg = _cfg(svc)
    s, e = repo.parse(row["start"]), repo.parse(row["end"])
    if decision not in ("confirm", "waitlist", "decline"):
        raise ServiceError("INVALID", "decision must be confirm, waitlist or decline")
    if row["status"] not in OPEN:
        raise ServiceError("NOT_OPEN", f"This request is already {row['status']}.", 409)

    result = None
    if decision in ("confirm", "waitlist"):
        target = "confirmed" if decision == "confirm" else "tentative"
        if row["commitment_id"]:  # waitlisted -> confirmed
            if decision == "waitlist":
                raise ServiceError("NOT_OPEN", "Already waitlisted.", 409)
            result = svc.submit("update_commitment", {"id": row["commitment_id"],
                                                      "changes": {"status": "confirmed"}}, "ui")
        else:
            result = svc.submit("create_commitment", _commitment_payload(row, s, e, target), "ui")
        if result["status"] != "executed":
            # Never displace anything on behalf of a public request: report and let the owner decide.
            if result.get("token"):
                svc.reject(result["token"])
            return {"status": "not_possible", "plan": result["plan"],
                    "message": "That time is taken now. Decline it, or free the time first."}
        cid = ser.parse_id(result["result"]["commitment"]["id"], "C")
        new_status = "confirmed" if decision == "confirm" else "waitlisted"
        _set_status(svc, num, new_status, commitment_id=cid)
        template, subject = (("confirmed", "Your meeting is confirmed") if decision == "confirm"
                             else ("waitlisted", "You're on the waitlist"))
    else:
        if row["commitment_id"]:
            c = repo.get_commitment(svc.conn, row["commitment_id"])
            if c and c.is_active:
                if c.status.value == "confirmed":
                    raise ServiceError("CANCEL_FIRST", "Cancel the confirmed meeting first, then decline.", 409)
                svc.submit("cancel_commitments", {"ids": [c.id]}, "ui")
        _set_status(svc, num, "declined", reason=(reason or None), share_reason=1 if share_reason else 0)
        template, subject = "declined", "About your meeting request"

    row = dict(svc.conn.execute("SELECT * FROM public_requests WHERE id = ?", (num,)).fetchone())
    values = _mail_values(svc, cfg, row)
    if template == "declined":
        values["reason_html"] = (f'<p style="margin:0 0 16px 0;"><em>“{html.escape(reason)}”</em></p>'
                                 if share_reason and reason else "")
    with write_tx(svc.conn):
        mailer.queue(svc.conn, row["email"], subject, template, **values)
    return {"status": row["status"], "request": f"Q{num}", "commitment": result and result.get("result")}
