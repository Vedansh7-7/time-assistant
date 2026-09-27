"""Guardrail layer: the single entry point for every client (web UI, REST, AI tools, MCP).

All consequential operations follow the same path:

    submit(kind, payload, channel)
        -> plan (deterministic core)
        -> not executable?            return plan + alternatives/options
        -> confirmation == none?      re-plan + apply atomically
        -> otherwise                  store a pending action, return its token

    confirm(token, ...)
        -> verify the confirmation (button / clear reply / exact typed phrase)
        -> re-plan inside the write transaction
        -> refuse if the effects changed since the user saw them
        -> apply

No interface implements its own scheduling logic.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..core import confirm as confirm_mod
from ..core import policy
from ..core.model import (
    DEFAULT_LEVEL, Commitment, Freq, Occurrence, Recurrence, ReminderSpec, Status, Task,
)
from ..core.planner import Plan, PlanError, plan_cancel, plan_create, plan_force, plan_update
from ..db import repo
from ..db.connection import utcnow, write_tx
from . import serialize as ser
from . import settings as settings_mod

UTC = timezone.utc
PENDING_TTL = timedelta(minutes=30)
_write_lock = threading.RLock()


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, **self.details}


@dataclass
class _Action:
    plan: Callable[["CoreService", dict, str], Plan]
    execute: Callable[["CoreService", Plan, dict], dict]


class CoreService:
    def __init__(self, conn: sqlite3.Connection, now: Callable[[], datetime] = utcnow):
        self.conn = conn
        self._now = now
        self.tz: ZoneInfo = settings_mod.tz(conn)
        self.search = settings_mod.search_settings(conn)

    # ------------------------------------------------------------------ helpers
    def now(self) -> datetime:
        return self._now()

    def people_names(self) -> dict[int, str]:
        return {p["id"]: p["name"] for p in repo.list_people(self.conn)}

    def _dt(self, value) -> datetime:
        if isinstance(value, datetime):
            return ser.to_aware(value, self.tz)
        try:
            return ser.to_aware(datetime.fromisoformat(str(value)), self.tz)
        except ValueError:
            raise ServiceError("INVALID_TIME", f"Not an ISO datetime: {value!r}")

    def _require_commitment(self, cid) -> Commitment:
        try:
            num = ser.parse_id(cid, "C")
        except ValueError as e:
            raise ServiceError("INVALID_ID", str(e))
        c = repo.get_commitment(self.conn, num)
        if c is None:
            raise ServiceError("NOT_FOUND", f"Commitment C{num} not found", 404)
        return c

    def _person_ids(self, values) -> tuple[int, ...]:
        out = []
        for v in values or ():
            try:
                pid = ser.parse_id(v, "P")
            except ValueError as e:
                raise ServiceError("INVALID_ID", str(e))
            if repo.get_person(self.conn, pid) is None:
                raise ServiceError("NOT_FOUND", f"Person P{pid} not found", 404)
            out.append(pid)
        return tuple(sorted(set(out)))

    def _recurrence(self, d: dict | None) -> Recurrence | None:
        if not d:
            return None
        try:
            return Recurrence(
                freq=Freq(d["freq"]), interval=int(d.get("interval") or 1),
                by_weekday=tuple(int(x) for x in d.get("by_weekday") or ()),
                until=self._dt(d["until"]) if d.get("until") else None,
                count=int(d["count"]) if d.get("count") else None,
            )
        except (KeyError, ValueError) as e:
            raise ServiceError("INVALID_RECURRENCE", f"Invalid recurrence: {e}")

    def _reminders(self, value) -> tuple[ReminderSpec, ...] | None:
        if value is None:
            return None
        out = []
        for r in value:
            if r.get("offset_min") is not None:
                if int(r["offset_min"]) < 0:
                    raise ServiceError("INVALID_REMINDER", "Reminder offset cannot be negative")
                out.append(ReminderSpec(offset_min=int(r["offset_min"]), label=r.get("label")))
            elif r.get("at"):
                out.append(ReminderSpec(at=self._dt(r["at"]), label=r.get("label")))
            else:
                raise ServiceError("INVALID_REMINDER", "Reminder needs offset_min or at")
        return tuple(out)

    def _task_id(self, value) -> int | None:
        if value in (None, ""):
            return None
        tid = ser.parse_id(value, "T")
        if repo.get_task(self.conn, tid) is None:
            raise ServiceError("NOT_FOUND", f"Task T{tid} not found", 404)
        return tid

    def commitment_from_payload(self, p: dict, base: Commitment | None = None) -> Commitment:
        """Build a candidate from a JSON payload; `base` supplies unchanged fields for edits."""
        def pick(key, conv=lambda x: x, default=None):
            if key in p:
                return conv(p[key]) if p[key] is not None or key in ("reminders", "recurrence") else None
            return getattr(base, key) if base is not None else default

        try:
            status = Status(p["status"]) if "status" in p else (base.status if base else Status.TENTATIVE)
        except ValueError:
            raise ServiceError("INVALID_STATUS", f"Unknown status {p.get('status')!r}")
        start = self._dt(p["start"]) if "start" in p else (base.start if base else None)
        end = self._dt(p["end"]) if "end" in p else (base.end if base else None)
        if start is None or end is None:
            raise ServiceError("INVALID_TIME", "start and end are required")
        if "duration_min" in p and "end" not in p:
            end = start + timedelta(minutes=int(p["duration_min"]))
        elif base is not None and "start" in p and "end" not in p:
            end = start + (base.end - base.start)  # moving keeps the duration
        return Commitment(
            id=base.id if base else None,
            title=pick("title", str, ""),
            kind=pick("kind", str, "meeting") or "meeting",
            start=start, end=end, status=status,
            authority_level=self._level(p, base),
            person_ids=self._person_ids(p["person_ids"]) if "person_ids" in p else (base.person_ids if base else ()),
            location=pick("location"), notes=pick("notes"),
            buffer_before_min=int(pick("buffer_before_min", int, 0) or 0),
            buffer_after_min=int(pick("buffer_after_min", int, 0) or 0),
            recurrence=self._recurrence(p["recurrence"]) if "recurrence" in p else (base.recurrence if base else None),
            exdates=base.exdates if base else frozenset(),
            reminders=self._reminders(p["reminders"]) if "reminders" in p else (base.reminders if base else None),
            task_id=self._task_id(p["task_id"]) if "task_id" in p else (base.task_id if base else None),
        )

    @staticmethod
    def _level(p: dict, base: Commitment | None) -> int:
        value = p.get("authority_level") if "authority_level" in p else (base.authority_level if base else None)
        if value is None:
            return DEFAULT_LEVEL
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ServiceError("INVALID_LEVEL", f"Authority level must be a number, got {value!r}")

    def _plan_dict(self, plan: Plan) -> dict:
        return ser.plan(plan, self.tz, self.people_names())

    # ------------------------------------------------------------------ planners / executors
    def _plan_create(self, p: dict, channel: str) -> Plan:
        c = self.commitment_from_payload(p)
        return plan_create(c, repo.active_commitments(self.conn), repo.list_rules(self.conn), self.tz, self.now(),
                           self.search, channel, frozenset(p.get("displace") or ()))

    def _plan_update(self, p: dict, channel: str) -> Plan:
        original = self._require_commitment(p.get("id"))
        changes = dict(p.get("changes") or {})
        updated = self.commitment_from_payload(changes, base=original)
        return plan_update(original, updated, repo.active_commitments(self.conn), repo.list_rules(self.conn),
                           self.tz, self.now(), self.search, channel, frozenset(p.get("displace") or ()))

    def _occurrence(self, c: Commitment, occ_start: datetime) -> Occurrence:
        from ..core.recurrence import expand
        if c.recurrence is None:
            raise ServiceError("NOT_RECURRING", "Only recurring commitments have occurrences")
        for o in expand(c, occ_start - timedelta(minutes=1), occ_start + timedelta(minutes=1), self.tz):
            if o.start == occ_start:
                return o
        raise ServiceError("NOT_FOUND", "No occurrence starts at that time", 404)

    def _plan_move_occurrence(self, p: dict, channel: str) -> Plan:
        """Edit one occurrence of a series: it is detached into a standalone commitment."""
        series = self._require_commitment(p.get("id"))
        occ = self._occurrence(series, self._dt(p.get("occurrence_start")))
        original = series.with_(id=None, start=occ.start, end=occ.end, recurrence=None, exdates=frozenset())
        updated = self.commitment_from_payload(dict(p.get("changes") or {}), base=original)
        existing = [series.with_(exdates=series.exdates | {occ.start}) if c.id == series.id else c
                    for c in repo.active_commitments(self.conn)]
        plan = plan_update(original, updated, existing, repo.list_rules(self.conn), self.tz, self.now(),
                           self.search, channel, frozenset(p.get("displace") or ()))
        plan.action = "move_occurrence"
        return plan

    def _plan_cancel(self, p: dict, channel: str) -> Plan:
        ids = p.get("ids") or ([p["id"]] if "id" in p else [])
        if not ids:
            raise ServiceError("INVALID", "No commitments given")
        return plan_cancel([self._require_commitment(i) for i in ids], channel)

    def _plan_force(self, p: dict, channel: str) -> Plan:
        c = self.commitment_from_payload(p)
        return plan_force(c, repo.active_commitments(self.conn), repo.list_rules(self.conn), self.tz, self.now(),
                          self.search)

    def _plan_skip(self, p: dict, channel: str) -> Plan:
        c = self._require_commitment(p.get("id"))
        occ = self._occurrence(c, self._dt(p.get("occurrence_start")))
        return Plan("skip_occurrence", "OK", True, policy.for_cancel(channel, c.status),
                    f"Skip {c.title} on {ser.human_span(occ.start, occ.end, self.tz)}?", original=c)

    def _plan_person(self, p: dict, channel: str) -> Plan:
        pid = ser.parse_id(p.get("id"), "P")
        person = repo.get_person(self.conn, pid)
        if person is None:
            raise ServiceError("NOT_FOUND", f"Person P{pid} not found", 404)
        changes = {k: v for k, v in (p.get("changes") or {}).items() if k in repo.PERSON_FIELDS}
        if not changes:
            raise ServiceError("INVALID", "No profile fields to change")
        desc = "; ".join(f"{k}: {v!r}" for k, v in changes.items())
        return Plan("update_person", "OK", True, policy.for_profile_update(channel),
                    f"Update {person['name']}'s profile: {desc}")

    def _plan_create_person(self, p: dict, channel: str) -> Plan:
        name = (p.get("name") or "").strip()
        if not name:
            raise ServiceError("INVALID", "Name is required")
        if repo.find_people(self.conn, name) and any(x["name"].lower() == name.lower()
                                                     for x in repo.find_people(self.conn, name)):
            raise ServiceError("EXISTS", f"{name} already exists", 409)
        return Plan("create_person", "OK", True, policy.for_profile_update(channel), f"Add {name} to people?")

    def _plan_delete_tasks(self, p: dict, channel: str) -> Plan:
        ids = [ser.parse_id(i, "T") for i in (p.get("ids") or [])]
        tasks = [repo.get_task(self.conn, i) for i in ids]
        if not ids or any(t is None for t in tasks):
            raise ServiceError("NOT_FOUND", "Task not found", 404)
        n = len(ids)
        phrase = policy.typed_phrase("delete", n, "tasks") if n > 1 else None
        return Plan("delete_tasks", "OK", True, policy.for_task_delete(n),
                    (f"Delete {tasks[0].title}?" if n == 1 else f"Delete {n} tasks? Type \"{phrase}\" to confirm."),
                    typed_phrase=phrase)

    def _displace(self, occ: Occurrence, by_id: int) -> None:
        c = repo.get_commitment(self.conn, occ.commitment.id)
        if c.recurrence is None:
            repo.update_commitment(self.conn, c.with_(status=Status.NEEDS_RESCHEDULE), displaced_by=by_id)
            return
        # Recurring: remove just this occurrence and keep a standalone copy to reschedule.
        repo.update_commitment(self.conn, c.with_(exdates=c.exdates | {occ.start}))
        copy = c.with_(id=None, start=occ.start, end=occ.end, recurrence=None, exdates=frozenset(),
                       status=Status.NEEDS_RESCHEDULE)
        new_id = repo.insert_commitment(self.conn, copy)
        repo.update_commitment(self.conn, copy.with_(id=new_id), displaced_by=by_id)

    def _exec_create(self, plan: Plan, p: dict) -> dict:
        cid = repo.insert_commitment(self.conn, plan.candidate)
        for occ in plan.displace:
            self._displace(occ, cid)
        return {"commitment": ser.commitment(repo.get_commitment(self.conn, cid), self.tz, self.people_names()),
                "displaced": [o.key for o in plan.displace]}

    def _exec_update(self, plan: Plan, p: dict) -> dict:
        repo.update_commitment(self.conn, plan.candidate)
        for occ in plan.displace:
            self._displace(occ, plan.candidate.id)
        return {"commitment": ser.commitment(repo.get_commitment(self.conn, plan.candidate.id), self.tz,
                                             self.people_names()),
                "displaced": [o.key for o in plan.displace]}

    def _exec_move_occurrence(self, plan: Plan, p: dict) -> dict:
        series = repo.get_commitment(self.conn, ser.parse_id(p["id"], "C"))
        repo.update_commitment(self.conn, series.with_(exdates=series.exdates | {plan.original.start}))
        cid = repo.insert_commitment(self.conn, plan.candidate)
        for occ in plan.displace:
            self._displace(occ, cid)
        return {"commitment": ser.commitment(repo.get_commitment(self.conn, cid), self.tz, self.people_names()),
                "displaced": [o.key for o in plan.displace]}

    def _exec_cancel(self, plan: Plan, p: dict) -> dict:
        for c in plan.cancel:
            repo.update_commitment(self.conn, c.with_(status=Status.CANCELLED))
        return {"cancelled": [f"C{c.id}" for c in plan.cancel]}

    def _exec_skip(self, plan: Plan, p: dict) -> dict:
        c = repo.get_commitment(self.conn, plan.original.id)
        occ_start = self._dt(p["occurrence_start"])
        repo.update_commitment(self.conn, c.with_(exdates=c.exdates | {occ_start}))
        return {"skipped": f"C{c.id}@{occ_start.strftime('%Y%m%dT%H%MZ')}"}

    def _exec_person(self, plan: Plan, p: dict) -> dict:
        pid = ser.parse_id(p["id"], "P")
        repo.update_person(self.conn, pid, {k: v for k, v in p["changes"].items() if k in repo.PERSON_FIELDS})
        return {"person": ser.person(repo.get_person(self.conn, pid))}

    def _exec_create_person(self, plan: Plan, p: dict) -> dict:
        data = {k: v for k, v in p.items() if k in repo.PERSON_FIELDS}
        data["name"] = data["name"].strip()
        pid = repo.insert_person(self.conn, data)
        return {"person": ser.person(repo.get_person(self.conn, pid))}

    def _exec_delete_tasks(self, plan: Plan, p: dict) -> dict:
        ids = [ser.parse_id(i, "T") for i in p["ids"]]
        for i in ids:
            repo.delete_task(self.conn, i)
        return {"deleted": [f"T{i}" for i in ids]}

    ACTIONS: dict[str, _Action] = {
        "create_commitment": _Action(_plan_create, _exec_create),
        "update_commitment": _Action(_plan_update, _exec_update),
        "move_occurrence": _Action(_plan_move_occurrence, _exec_move_occurrence),
        "cancel_commitments": _Action(_plan_cancel, _exec_cancel),
        "force_commitment": _Action(_plan_force, _exec_create),
        "skip_occurrence": _Action(_plan_skip, _exec_skip),
        "update_person": _Action(_plan_person, _exec_person),
        "create_person": _Action(_plan_create_person, _exec_create_person),
        "delete_tasks": _Action(_plan_delete_tasks, _exec_delete_tasks),
    }

    def _action(self, kind: str) -> _Action:
        if kind not in self.ACTIONS:
            raise ServiceError("UNKNOWN_ACTION", f"Unknown action {kind!r}")
        return self.ACTIONS[kind]

    def _plan(self, kind: str, payload: dict, channel: str) -> Plan:
        try:
            return self._action(kind).plan(self, payload, channel)
        except PlanError as e:
            raise ServiceError(e.code, e.message)

    @staticmethod
    def _fingerprint(plan: Plan, payload: dict) -> str:
        blob = plan.fingerprint() + json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    # ------------------------------------------------------------------ public API
    def check(self, kind: str, payload: dict, channel: str = "ui") -> dict:
        """Plan only (READ/PROPOSE). Never writes."""
        if channel not in ("ui", "ai"):
            raise ServiceError("INVALID", "channel must be ui or ai")
        return self._plan_dict(self._plan(kind, payload, channel))

    def submit(self, kind: str, payload: dict, channel: str = "ui") -> dict:
        if channel not in ("ui", "ai"):
            raise ServiceError("INVALID", "channel must be ui or ai")
        with _write_lock, write_tx(self.conn):
            plan = self._plan(kind, payload, channel)
            if not plan.executable:
                return {"status": "rejected", "plan": self._plan_dict(plan)}
            if plan.confirmation == policy.NONE:
                result = self._action(kind).execute(self, plan, payload)
                return {"status": "executed", "plan": self._plan_dict(plan), "result": result}
            token = "A" + secrets.token_urlsafe(9)
            repo.insert_pending(self.conn, {
                "token": token, "kind": kind, "channel": channel, "payload": payload,
                "fingerprint": self._fingerprint(plan, payload), "confirmation": plan.confirmation,
                "typed_phrase": plan.typed_phrase, "summary": plan.message,
                "expires_at": self.now() + PENDING_TTL,
            })
            return {"status": "needs_confirmation", "token": token, "confirmation": plan.confirmation,
                    "typed_phrase": plan.typed_phrase, "plan": self._plan_dict(plan)}

    def confirm(self, token: str, *, via: str, text: str | None = None) -> dict:
        """Execute a pending action.

        via="button": explicit click in the web UI (not allowed for typed confirmations).
        via="reply":  the user's own chat message; must classify as an unambiguous yes.
        via="typed":  the exact phrase shown to the user.
        """
        with _write_lock, write_tx(self.conn):
            repo.expire_pending(self.conn, self.now())
            row = repo.get_pending(self.conn, token)
            if row is None:
                raise ServiceError("NOT_FOUND", "Unknown confirmation token", 404)
            if row["status"] != "pending":
                raise ServiceError("NOT_PENDING", f"This action is already {row['status']}.", 409)

            if row["confirmation"] == policy.TYPED:
                if via not in ("typed", "reply") or not confirm_mod.matches_typed_phrase(text or "", row["typed_phrase"]):
                    raise ServiceError("TYPED_CONFIRMATION_REQUIRED",
                                       f"Type exactly: {row['typed_phrase']}", 400,
                                       {"typed_phrase": row["typed_phrase"]})
            elif via == "reply":
                verdict = confirm_mod.classify_reply(text or "")
                if verdict == confirm_mod.DENY:
                    repo.resolve_pending(self.conn, token, "rejected")
                    return {"status": "rejected", "message": "Okay, I won't."}
                if verdict != confirm_mod.AFFIRM:
                    raise ServiceError("AMBIGUOUS_CONFIRMATION",
                                       "That wasn't a clear yes. Should I go ahead?", 400)
            elif via not in ("button", "typed"):
                raise ServiceError("INVALID", "via must be button, reply or typed")

            plan = self._plan(row["kind"], row["payload"], row["channel"])
            if not plan.executable or self._fingerprint(plan, row["payload"]) != row["fingerprint"]:
                repo.resolve_pending(self.conn, token, "superseded")
                return {"status": "superseded",
                        "message": "Your schedule changed since this was suggested, so nothing was done. Please check it again.",
                        "plan": self._plan_dict(plan)}
            result = self._action(row["kind"]).execute(self, plan, row["payload"])
            repo.resolve_pending(self.conn, token, "executed")
            return {"status": "executed", "result": result, "plan": self._plan_dict(plan)}

    def reject(self, token: str) -> dict:
        with _write_lock, write_tx(self.conn):
            row = repo.get_pending(self.conn, token)
            if row is None:
                raise ServiceError("NOT_FOUND", "Unknown confirmation token", 404)
            if row["status"] == "pending":
                repo.resolve_pending(self.conn, token, "rejected")
            return {"status": "rejected"}

    def pending(self, channel: str | None = None) -> list[dict]:
        with _write_lock, write_tx(self.conn):
            repo.expire_pending(self.conn, self.now())
        out = []
        for r in repo.list_pending(self.conn, channel):
            d = {k: r[k] for k in ("token", "kind", "channel", "confirmation", "typed_phrase", "summary",
                                   "created_at", "expires_at")}
            d["candidate"], d["replaces"] = None, []
            try:  # describe what it would do now, so clients can phrase a plain question
                plan = self._plan(r["kind"], r["payload"], r["channel"])
                if plan.candidate is not None:
                    d["candidate"] = {"title": plan.candidate.title,
                                      "when": ser.human_span(plan.candidate.start, plan.candidate.end, self.tz)}
                d["replaces"] = [o.commitment.title for o in plan.displace]
            except ServiceError:
                pass  # target vanished; confirm will report it
            out.append(d)
        return out

    # ------------------------------------------------------------------ reads
    def schedule(self, start: datetime, end: datetime, include_inactive: bool = False) -> list[dict]:
        from ..core.recurrence import expand
        statuses = None if include_inactive else ["tentative", "confirmed"]
        people = self.people_names()
        out: list[Occurrence] = []
        for c in repo.list_commitments(self.conn, start, end, statuses):
            out.extend(expand(c, start, end, self.tz))
        out.sort(key=lambda o: o.start)
        return [ser.occurrence(o, self.tz, people) for o in out if o.start < end and o.end > start]

    def attention(self) -> list[dict]:
        """Commitments that need the user's attention (displaced ones)."""
        people = self.people_names()
        return [ser.commitment(c, self.tz, people)
                for c in repo.list_commitments(self.conn, statuses=["needs_reschedule"])]

    # ------------------------------------------------------------------ direct (non-gated) writes
    def create_task(self, p: dict) -> dict:
        t = self._task_from(p)
        with _write_lock, write_tx(self.conn):
            tid = repo.insert_task(self.conn, t)
        return ser.task(repo.get_task(self.conn, tid), self.tz)

    def update_task(self, tid, p: dict) -> dict:
        tid = ser.parse_id(tid, "T")
        with _write_lock, write_tx(self.conn):
            t = repo.get_task(self.conn, tid)
            if t is None:
                raise ServiceError("NOT_FOUND", f"Task T{tid} not found", 404)
            t = self._task_from(p, base=t)
            repo.update_task(self.conn, t)
        return ser.task(repo.get_task(self.conn, tid), self.tz)

    def _task_from(self, p: dict, base: Task | None = None) -> Task:
        title = p.get("title", base.title if base else "")
        if not title or not str(title).strip():
            raise ServiceError("INVALID", "Task title is required")
        deadline = self._dt(p["deadline"]) if "deadline" in p else (base.deadline if base else None)
        if deadline is None:
            raise ServiceError("INVALID", "Task deadline is required")
        dur = int(p.get("estimated_duration_min", base.estimated_duration_min if base else 0) or 0)
        if dur <= 0:
            raise ServiceError("INVALID", "Estimated duration must be positive")
        status = p.get("status", base.status if base else "open")
        if status not in ("open", "done", "dropped"):
            raise ServiceError("INVALID", "Task status must be open, done or dropped")
        return Task(base.id if base else None, str(title), deadline, dur, status, p.get("notes", base.notes if base else None))

    def upsert_person_direct(self, p: dict, pid=None) -> dict:
        """Manual (UI) person edits. AI edits go through submit('update_person', ..., 'ai')."""
        data = {k: v for k, v in p.items() if k in repo.PERSON_FIELDS}
        with _write_lock, write_tx(self.conn):
            if pid is None:
                if not (data.get("name") or "").strip():
                    raise ServiceError("INVALID", "Name is required")
                try:
                    pid = repo.insert_person(self.conn, data)
                except sqlite3.IntegrityError:
                    raise ServiceError("EXISTS", f"{data['name']} already exists", 409)
            else:
                pid = ser.parse_id(pid, "P")
                if repo.get_person(self.conn, pid) is None:
                    raise ServiceError("NOT_FOUND", f"Person P{pid} not found", 404)
                repo.update_person(self.conn, pid, data)
        return ser.person(repo.get_person(self.conn, pid))
