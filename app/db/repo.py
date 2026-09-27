"""Row <-> core-object mapping. The only module that writes SQL for domain data."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from ..core.model import (
    AvailabilityRule, Commitment, Freq, Recurrence, ReminderSpec, RuleKind, Status, Task,
)
from .connection import utcnow

UTC = timezone.utc


# ---------- datetime helpers ----------

def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("naive datetime reached the repository")
    return dt.astimezone(UTC).isoformat()


def parse(s: str | None) -> datetime | None:
    if s is None:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _now() -> str:
    return iso(utcnow())


# ---------- recurrence / reminder JSON ----------

def recurrence_to_json(r: Recurrence | None) -> str | None:
    if r is None:
        return None
    return json.dumps({"freq": r.freq.value, "interval": r.interval, "by_weekday": list(r.by_weekday),
                       "until": iso(r.until), "count": r.count})


def recurrence_from_json(s: str | None) -> Recurrence | None:
    if not s:
        return None
    d = json.loads(s)
    return Recurrence(Freq(d["freq"]), d.get("interval", 1), tuple(d.get("by_weekday") or ()),
                      parse(d.get("until")), d.get("count"))


def reminders_to_json(r: tuple[ReminderSpec, ...] | None) -> str | None:
    if r is None:
        return None
    return json.dumps([{"offset_min": x.offset_min, "at": iso(x.at), "label": x.label} for x in r])


def reminders_from_json(s: str | None) -> tuple[ReminderSpec, ...] | None:
    if s is None:
        return None
    return tuple(ReminderSpec(d.get("offset_min"), parse(d.get("at")), d.get("label")) for d in json.loads(s))


# ---------- commitments ----------

def _commitment(row: sqlite3.Row, people: tuple[int, ...]) -> Commitment:
    return Commitment(
        id=row["id"], title=row["title"], kind=row["kind"],
        start=parse(row["start"]), end=parse(row["end"]),
        status=Status(row["status"]), authority_level=row["authority_level"],
        person_ids=people, location=row["location"], notes=row["notes"],
        buffer_before_min=row["buffer_before_min"], buffer_after_min=row["buffer_after_min"],
        recurrence=recurrence_from_json(row["recurrence"]),
        exdates=frozenset(parse(x) for x in json.loads(row["exdates"] or "[]")),
        reminders=reminders_from_json(row["reminders"]), task_id=row["task_id"],
    )


def _people_map(conn: sqlite3.Connection, ids: Iterable[int]) -> dict[int, tuple[int, ...]]:
    ids = list(ids)
    if not ids:
        return {}
    out: dict[int, list[int]] = {}
    q = f"SELECT commitment_id, person_id FROM commitment_people WHERE commitment_id IN ({','.join('?' * len(ids))})"
    for r in conn.execute(q, ids):
        out.setdefault(r[0], []).append(r[1])
    return {k: tuple(sorted(v)) for k, v in out.items()}


def _rows_to_commitments(conn, rows) -> list[Commitment]:
    rows = list(rows)
    pm = _people_map(conn, [r["id"] for r in rows])
    return [_commitment(r, pm.get(r["id"], ())) for r in rows]


def get_commitment(conn, cid: int) -> Commitment | None:
    row = conn.execute("SELECT * FROM commitments WHERE id = ?", (cid,)).fetchone()
    return _rows_to_commitments(conn, [row])[0] if row else None


def active_commitments(conn) -> list[Commitment]:
    """Everything that can occupy time. The engine expands recurrences itself."""
    rows = conn.execute("SELECT * FROM commitments WHERE status IN ('tentative','confirmed')")
    return _rows_to_commitments(conn, rows)


def list_commitments(conn, start: datetime | None = None, end: datetime | None = None,
                     statuses: Iterable[str] | None = None, person_id: int | None = None,
                     task_id: int | None = None, include_recurring: bool = True) -> list[Commitment]:
    """Commitments whose stored span touches [start, end). Recurring series are
    always included (when requested) because their occurrences may fall anywhere."""
    q = ["SELECT DISTINCT c.* FROM commitments c"]
    args: list[Any] = []
    where = []
    if person_id is not None:
        q.append("JOIN commitment_people cp ON cp.commitment_id = c.id")
        where.append("cp.person_id = ?")
        args.append(person_id)
    if statuses:
        statuses = list(statuses)
        where.append(f"c.status IN ({','.join('?' * len(statuses))})")
        args.extend(statuses)
    if task_id is not None:
        where.append("c.task_id = ?")
        args.append(task_id)
    time_clauses = []
    if start is not None and end is not None:
        time_clauses.append("(c.start < ? AND c.end > ?)")
        args.extend([iso(end), iso(start)])
        if include_recurring:
            time_clauses.append("c.recurrence IS NOT NULL")
    if time_clauses:
        where.append("(" + " OR ".join(time_clauses) + ")")
    if where:
        q.append("WHERE " + " AND ".join(where))
    q.append("ORDER BY c.start")
    return _rows_to_commitments(conn, conn.execute(" ".join(q), args))


def insert_commitment(conn, c: Commitment) -> int:
    now = _now()
    cur = conn.execute(
        """INSERT INTO commitments (title, kind, start, end, status, authority_level, location, notes,
               buffer_before_min, buffer_after_min, recurrence, exdates, reminders, task_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (c.title.strip(), c.kind, iso(c.start), iso(c.end), c.status.value, c.authority_level, c.location,
         c.notes, c.buffer_before_min, c.buffer_after_min, recurrence_to_json(c.recurrence),
         json.dumps(sorted(iso(x) for x in c.exdates)), reminders_to_json(c.reminders), c.task_id, now, now),
    )
    cid = cur.lastrowid
    _set_people(conn, cid, c.person_ids)
    return cid


def update_commitment(conn, c: Commitment, displaced_by: int | None = None) -> None:
    assert c.id is not None
    conn.execute(
        """UPDATE commitments SET title=?, kind=?, start=?, end=?, status=?, authority_level=?, location=?, notes=?,
               buffer_before_min=?, buffer_after_min=?, recurrence=?, exdates=?, reminders=?, task_id=?,
               displaced_by=COALESCE(?, displaced_by), updated_at=? WHERE id=?""",
        (c.title.strip(), c.kind, iso(c.start), iso(c.end), c.status.value, c.authority_level, c.location, c.notes,
         c.buffer_before_min, c.buffer_after_min, recurrence_to_json(c.recurrence),
         json.dumps(sorted(iso(x) for x in c.exdates)), reminders_to_json(c.reminders), c.task_id,
         displaced_by, _now(), c.id),
    )
    _set_people(conn, c.id, c.person_ids)


def displaced_by(conn, cid: int) -> int | None:
    row = conn.execute("SELECT displaced_by FROM commitments WHERE id = ?", (cid,)).fetchone()
    return row[0] if row else None


def _set_people(conn, cid: int, person_ids: tuple[int, ...]) -> None:
    conn.execute("DELETE FROM commitment_people WHERE commitment_id = ?", (cid,))
    for pid in set(person_ids):
        conn.execute("INSERT INTO commitment_people (commitment_id, person_id) VALUES (?, ?)", (cid, pid))


# ---------- people ----------

PERSON_FIELDS = ("name", "aliases", "relationship", "importance", "communication_style",
                 "preferences", "custom_instructions", "notes")


def list_people(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE")]


def get_person(conn, pid: int) -> dict | None:
    r = conn.execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone()
    return dict(r) if r else None


def find_people(conn, query: str) -> list[dict]:
    """Exact name/alias matches first, then prefix matches. Case-insensitive."""
    q = query.strip().lower()
    if not q:
        return []
    exact, prefix = [], []
    for p in list_people(conn):
        names = [p["name"].lower()] + [a.strip().lower() for a in (p["aliases"] or "").split(",") if a.strip()]
        if q in names:
            exact.append(p)
        elif any(n.startswith(q) or n.split(" ")[0] == q for n in names):
            prefix.append(p)
    return exact or prefix


def insert_person(conn, data: dict) -> int:
    now = _now()
    cols = [f for f in PERSON_FIELDS if f in data]
    vals = [data[f] if data[f] is not None or f != "aliases" else "" for f in cols]
    cur = conn.execute(
        f"INSERT INTO people ({','.join(cols)}, created_at, updated_at) VALUES ({','.join('?' * len(cols))}, ?, ?)",
        (*vals, now, now),
    )
    return cur.lastrowid


def update_person(conn, pid: int, changes: dict) -> None:
    cols = [f for f in PERSON_FIELDS if f in changes]
    if not cols:
        return
    sets = ", ".join(f"{c} = ?" for c in cols)
    vals = [changes[c] if not (c == "aliases" and changes[c] is None) else "" for c in cols]
    conn.execute(f"UPDATE people SET {sets}, updated_at = ? WHERE id = ?", (*vals, _now(), pid))


def delete_person(conn, pid: int) -> None:
    conn.execute("DELETE FROM people WHERE id = ?", (pid,))


# ---------- tasks ----------

def _task(r) -> Task:
    return Task(r["id"], r["title"], parse(r["deadline"]), r["estimated_duration_min"], r["status"], r["notes"])


def list_tasks(conn, statuses: Iterable[str] | None = None) -> list[Task]:
    if statuses:
        statuses = list(statuses)
        rows = conn.execute(f"SELECT * FROM tasks WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY deadline",
                            statuses)
    else:
        rows = conn.execute("SELECT * FROM tasks ORDER BY deadline")
    return [_task(r) for r in rows]


def get_task(conn, tid: int) -> Task | None:
    r = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    return _task(r) if r else None


def insert_task(conn, t: Task) -> int:
    now = _now()
    return conn.execute(
        "INSERT INTO tasks (title, deadline, estimated_duration_min, status, notes, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (t.title.strip(), iso(t.deadline), t.estimated_duration_min, t.status, t.notes, now, now),
    ).lastrowid


def update_task(conn, t: Task) -> None:
    conn.execute(
        "UPDATE tasks SET title=?, deadline=?, estimated_duration_min=?, status=?, notes=?, updated_at=? WHERE id=?",
        (t.title.strip(), iso(t.deadline), t.estimated_duration_min, t.status, t.notes, _now(), t.id),
    )


def delete_task(conn, tid: int) -> None:
    conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))


# ---------- availability rules ----------

def _rule(r) -> AvailabilityRule:
    wd = tuple(int(x) for x in r["weekdays"].split(",") if x.strip())
    return AvailabilityRule(r["id"], RuleKind(r["kind"]), r["start_minute"], r["end_minute"], wd,
                            r["applies_to"], r["label"], r["weight"])


def list_rules(conn) -> list[AvailabilityRule]:
    return [_rule(r) for r in conn.execute("SELECT * FROM availability_rules ORDER BY start_minute")]


def get_rule(conn, rid: int) -> AvailabilityRule | None:
    r = conn.execute("SELECT * FROM availability_rules WHERE id = ?", (rid,)).fetchone()
    return _rule(r) if r else None


def upsert_rule(conn, rule: AvailabilityRule) -> int:
    now = _now()
    vals = (rule.kind.value, ",".join(str(d) for d in rule.weekdays), rule.start_minute, rule.end_minute,
            rule.applies_to, rule.label, rule.weight)
    if rule.id is None:
        return conn.execute(
            "INSERT INTO availability_rules (kind, weekdays, start_minute, end_minute, applies_to, label, weight, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (*vals, now, now)).lastrowid
    conn.execute("UPDATE availability_rules SET kind=?, weekdays=?, start_minute=?, end_minute=?, applies_to=?, "
                 "label=?, weight=?, updated_at=? WHERE id=?", (*vals, now, rule.id))
    return rule.id


def delete_rule(conn, rid: int) -> None:
    conn.execute("DELETE FROM availability_rules WHERE id = ?", (rid,))


# ---------- settings ----------

def get_setting(conn, key: str, default: Any = None) -> Any:
    r = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_setting(conn, key: str, value: Any) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value)))


# ---------- pending actions ----------

def insert_pending(conn, row: dict) -> None:
    conn.execute(
        "INSERT INTO pending_actions (token, kind, channel, payload, fingerprint, confirmation, typed_phrase, summary, "
        "status, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?, 'pending', ?, ?)",
        (row["token"], row["kind"], row["channel"], json.dumps(row["payload"]), row["fingerprint"],
         row["confirmation"], row.get("typed_phrase"), row["summary"], _now(), iso(row["expires_at"])),
    )


def get_pending(conn, token: str) -> dict | None:
    r = conn.execute("SELECT * FROM pending_actions WHERE token = ?", (token,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    return d


def list_pending(conn, channel: str | None = None) -> list[dict]:
    q = "SELECT * FROM pending_actions WHERE status = 'pending'"
    args: list = []
    if channel:
        q += " AND channel = ?"
        args.append(channel)
    rows = conn.execute(q + " ORDER BY created_at DESC", args)
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def resolve_pending(conn, token: str, status: str) -> None:
    conn.execute("UPDATE pending_actions SET status = ?, resolved_at = ? WHERE token = ?", (status, _now(), token))


def expire_pending(conn, now: datetime) -> None:
    conn.execute("UPDATE pending_actions SET status = 'expired', resolved_at = ? WHERE status = 'pending' AND expires_at < ?",
                 (iso(now), iso(now)))


# ---------- reminders ----------

def reminder_fired(conn, occurrence_key: str, fire_at: datetime) -> bool:
    return conn.execute("SELECT 1 FROM reminder_log WHERE occurrence_key = ? AND fire_at = ?",
                        (occurrence_key, iso(fire_at))).fetchone() is not None


def log_reminder(conn, occurrence_key: str, fire_at: datetime, commitment_id: int, message: str) -> None:
    conn.execute("INSERT OR IGNORE INTO reminder_log (occurrence_key, fire_at, commitment_id, message, fired_at) "
                 "VALUES (?,?,?,?,?)", (occurrence_key, iso(fire_at), commitment_id, message, _now()))


def active_notifications(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM reminder_log WHERE dismissed = 0 ORDER BY fire_at DESC LIMIT 50")]


def dismiss_notification(conn, occurrence_key: str, fire_at: str) -> None:
    conn.execute("UPDATE reminder_log SET dismissed = 1 WHERE occurrence_key = ? AND fire_at = ?",
                 (occurrence_key, fire_at))


# ---------- chat ----------

def append_chat(conn, conversation: str, role: str, content: dict) -> None:
    conn.execute("INSERT INTO chat_messages (conversation, role, content, created_at) VALUES (?,?,?,?)",
                 (conversation, role, json.dumps(content), _now()))


def recent_chat(conn, conversation: str, limit: int = 30) -> list[dict]:
    rows = conn.execute("SELECT content FROM chat_messages WHERE conversation = ? ORDER BY id DESC LIMIT ?",
                        (conversation, limit)).fetchall()
    return [json.loads(r[0]) for r in reversed(rows)]


def clear_chat(conn, conversation: str) -> None:
    conn.execute("DELETE FROM chat_messages WHERE conversation = ?", (conversation,))
