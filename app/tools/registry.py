"""Typed, narrow tools for the AI agent and MCP clients.

Every tool has a permission category:
  READ     free to call; never writes
  PROPOSE  plans only (check a slot, preview a change); never writes
  WRITE    goes through the guardrail layer with channel="ai"; consequential
           writes come back as `needs_confirmation` with a token
  DELETE   like WRITE, stricter confirmation (typed phrase for bulk)
  CONFIRM  executes a pending token, only if the *user's own latest message*
           (supplied by the runtime, never by the model) is an unambiguous yes

Tools never touch SQL directly; they call the same services as the REST API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from ..db import repo
from ..services import queries
from ..services import serialize as ser
from ..services.core import CoreService, ServiceError

READ, PROPOSE, WRITE, DELETE, CONFIRM = "READ", "PROPOSE", "WRITE", "DELETE", "CONFIRM"


@dataclass
class ToolContext:
    svc: CoreService
    privacy_mode: str = "minimal"
    user_message: str | None = None   # latest raw user text (runtime-supplied)
    reply_confirm_allowed: bool = True  # False for MCP: confirm in the web UI instead
    # Tokens shown to the user in the previous assistant turn: the only ones a
    # chat reply may confirm. A "yes" can never confirm something the user
    # has not yet been shown, or something from an older exchange.
    confirmable_tokens: set[str] = field(default_factory=set)
    created_tokens: list[str] = field(default_factory=list)
    executed: list[dict] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    category: str
    description: str
    parameters: dict
    handler: Callable[[ToolContext, dict], Any]
    mcp: bool = True

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": f"[{self.category}] {self.description}", "parameters": self.parameters}}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


DT = {"type": "string", "description": "ISO 8601 local datetime, e.g. 2026-09-29T18:00"}
CID = {"type": "string", "description": "Commitment id, e.g. C12"}
LEVEL = {"type": "integer", "minimum": 1, "maximum": 5, "description": "Authority level 1–5 (default 3)"}
REMINDERS = {
    "type": "array", "description": "Omit to use the user's defaults; [] for none.",
    "items": _obj({"offset_min": {"type": "integer", "minimum": 0}, "at": DT, "label": {"type": "string"}}),
}
RECURRENCE = _obj({
    "freq": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
    "interval": {"type": "integer", "minimum": 1},
    "by_weekday": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6}},
    "until": DT, "count": {"type": "integer", "minimum": 1},
}, ["freq"])
COMMITMENT_FIELDS = {
    "title": {"type": "string"},
    "start": DT, "end": DT,
    "duration_min": {"type": "integer", "description": "Alternative to end"},
    "status": {"type": "string", "enum": ["tentative", "confirmed"],
               "description": "confirmed when the user agreed to commit; tentative only if they asked to pencil it in"},
    "authority_level": LEVEL,
    "person_ids": {"type": "array", "items": {"type": "string"}, "description": "e.g. [\"P3\"]"},
    "location": {"type": "string"}, "notes": {"type": "string"},
    "buffer_before_min": {"type": "integer", "minimum": 0, "description": "Only if the user explicitly asked"},
    "buffer_after_min": {"type": "integer", "minimum": 0, "description": "Only if the user explicitly asked"},
    "reminders": REMINDERS,
    "kind": {"type": "string", "enum": ["meeting", "task_block", "personal"]},
    "task_id": {"type": "string", "description": "For task work blocks, e.g. T4"},
    "displace": {"type": "array", "items": {"type": "string"},
                 "description": "Occurrence keys the USER chose to displace in an equal-authority conflict"},
}
CHANGES = _obj({k: v for k, v in COMMITMENT_FIELDS.items() if k not in ("displace",)})


def _person_view(p: dict, privacy: str) -> dict:
    keep = ("id", "name", "aliases", "relationship", "importance", "communication_style", "preferences",
            "custom_instructions")
    if privacy == "full":
        keep = keep + ("notes",)
    return {k: v for k, v in ser.person(p).items() if k in keep}


def _occ_view(o: dict, privacy: str) -> dict:
    drop = () if privacy == "full" else ("location",)
    return {k: v for k, v in o.items() if k not in drop}


# ---------------------------------------------------------------- READ
def t_now(ctx: ToolContext, a: dict):
    now = ctx.svc.now().astimezone(ctx.svc.tz)
    return {"now": now.isoformat(), "weekday": now.strftime("%A"), "timezone": str(ctx.svc.tz)}


def t_find_person(ctx: ToolContext, a: dict):
    matches = repo.find_people(ctx.svc.conn, a["name"])
    return {"matches": [_person_view(p, ctx.privacy_mode) for p in matches],
            "note": None if matches else "No such person. You may offer to add them (create_person)."}


def t_get_people(ctx: ToolContext, a: dict):
    people = repo.list_people(ctx.svc.conn)
    if ctx.privacy_mode == "full":
        return [_person_view(p, "full") for p in people]
    return [{"id": f"P{p['id']}", "name": p["name"]} for p in people]


def t_get_person(ctx: ToolContext, a: dict):
    p = repo.get_person(ctx.svc.conn, ser.parse_id(a["person_id"], "P"))
    if p is None:
        raise ServiceError("NOT_FOUND", "Person not found", 404)
    return _person_view(p, ctx.privacy_mode)


def t_schedule(ctx: ToolContext, a: dict):
    s, e = ctx.svc._dt(a["start"]), ctx.svc._dt(a["end"])
    if e <= s or e - s > timedelta(days=31):
        raise ServiceError("INVALID_RANGE", "Range must be positive and at most 31 days")
    return {"occurrences": [_occ_view(o, ctx.privacy_mode) for o in ctx.svc.schedule(s, e)]}


def t_day(ctx: ToolContext, a: dict):
    return queries.day(ctx.svc, date.fromisoformat(a["date"]))


def t_commitment(ctx: ToolContext, a: dict):
    c = ctx.svc._require_commitment(a["commitment_id"])
    d = ser.commitment(c, ctx.svc.tz, ctx.svc.people_names())
    if ctx.privacy_mode != "full":
        d.pop("notes", None)
        d.pop("location", None)
    return d


def t_find_commitments(ctx: ToolContext, a: dict):
    now = ctx.svc.now()
    pid = ser.parse_id(a["person_id"], "P") if a.get("person_id") else None
    start = ctx.svc._dt(a["start"]) if a.get("start") else now - timedelta(days=1)
    end = ctx.svc._dt(a["end"]) if a.get("end") else now + timedelta(days=60)
    statuses = a.get("statuses") or ["tentative", "confirmed", "needs_reschedule"]
    rows = repo.list_commitments(ctx.svc.conn, start, end, statuses, person_id=pid)
    q = (a.get("title_contains") or "").lower()
    names = ctx.svc.people_names()
    return [ser.commitment(c, ctx.svc.tz, names) for c in rows if q in c.title.lower()]


def t_slots(ctx: ToolContext, a: dict):
    return {"slots": queries.find_available_slots(
        ctx.svc, int(a["duration_min"]), a.get("start"), a.get("end"), a.get("kind", "meeting"),
        int(a.get("buffer_before_min", 0)), int(a.get("buffer_after_min", 0)), a.get("near"), a.get("limit"))}


def t_rules(ctx: ToolContext, a: dict):
    return [ser.rule(r) for r in repo.list_rules(ctx.svc.conn)]


def t_tasks(ctx: ToolContext, a: dict):
    return queries.tasks(ctx.svc, bool(a.get("include_closed")))


def t_task(ctx: ToolContext, a: dict):
    return queries.task_detail(ctx.svc, a["task_id"])


def t_work_blocks(ctx: ToolContext, a: dict):
    return queries.work_block_options(ctx.svc, a["task_id"])


def t_attention(ctx: ToolContext, a: dict):
    return {"needs_reschedule": ctx.svc.attention(), "pending_confirmations": ctx.svc.pending()}


# ---------------------------------------------------------------- PROPOSE
def t_check(ctx: ToolContext, a: dict):
    return ctx.svc.check("create_commitment", a, "ai")


def t_check_update(ctx: ToolContext, a: dict):
    return ctx.svc.check("update_commitment", a, "ai")


# ---------------------------------------------------------------- WRITE / DELETE
def _submit(kind: str):
    def run(ctx: ToolContext, a: dict):
        r = ctx.svc.submit(kind, a, "ai")
        if r["status"] == "executed":
            ctx.executed.append(r)
        if r["status"] == "needs_confirmation":
            ctx.created_tokens.append(r["token"])
            r["instruction"] = ("Nothing has been done yet. Ask the user to confirm in plain words"
                                + (f" by typing exactly: {r['typed_phrase']}" if r.get("typed_phrase") else "")
                                + ". Then call confirm_pending with this token.")
        return r
    return run


def t_create_task(ctx: ToolContext, a: dict):
    return {"status": "executed", "task": ctx.svc.create_task(a),
            "instruction": "Tell the user the task was added."}


def t_update_task(ctx: ToolContext, a: dict):
    return {"status": "executed", "task": ctx.svc.update_task(a["task_id"], a.get("changes") or {}),
            "instruction": "Tell the user what changed."}


# ---------------------------------------------------------------- CONFIRM
def t_confirm(ctx: ToolContext, a: dict):
    if not ctx.reply_confirm_allowed:
        return {"status": "not_allowed",
                "message": "Confirmation must be given by the user in the web app (Pending actions)."}
    if a["token"] not in ctx.confirmable_tokens:
        return {"status": "not_allowed",
                "message": "This action has not been shown to the user yet. Describe it and ask them first."}
    r = ctx.svc.confirm(a["token"], via="reply", text=ctx.user_message or "")
    if r.get("status") == "executed":
        ctx.executed.append(r)
    return r


def t_reject(ctx: ToolContext, a: dict):
    return ctx.svc.reject(a["token"])


TOOLS: list[Tool] = [
    Tool("get_current_time", READ, "Current local date/time and timezone. Call before resolving relative dates.",
         _obj({}), t_now),
    Tool("find_person", READ, "Find people by name or alias (case-insensitive).",
         _obj({"name": {"type": "string"}}, ["name"]), t_find_person),
    Tool("get_people", READ, "List people.", _obj({}), t_get_people),
    Tool("get_person", READ, "A person's profile (AI context only; never changes scheduling).",
         _obj({"person_id": {"type": "string"}}, ["person_id"]), t_get_person),
    Tool("get_schedule", READ, "Active occurrences between two local datetimes (max 31 days).",
         _obj({"start": DT, "end": DT}, ["start", "end"]), t_schedule),
    Tool("get_day", READ, "Busy/free/blocked timeline for a date (YYYY-MM-DD).",
         _obj({"date": {"type": "string"}}, ["date"]), t_day),
    Tool("get_commitment", READ, "One commitment by id.", _obj({"commitment_id": CID}, ["commitment_id"]),
         t_commitment),
    Tool("find_commitments", READ, "Search commitments by person, title text and/or date range.",
         _obj({"person_id": {"type": "string"}, "title_contains": {"type": "string"}, "start": DT, "end": DT,
               "statuses": {"type": "array", "items": {"type": "string"}}}), t_find_commitments),
    Tool("find_available_slots", READ,
         "Ranked free slots (respects commitments, buffers and the user's availability rules). "
         "Use `near` to rank around a requested time.",
         _obj({"duration_min": {"type": "integer", "minimum": 5}, "start": DT, "end": DT, "near": DT,
               "kind": {"type": "string", "enum": ["meeting", "task_block", "personal"]},
               "buffer_before_min": {"type": "integer"}, "buffer_after_min": {"type": "integer"},
               "limit": {"type": "integer", "maximum": 10}}, ["duration_min"]), t_slots),
    Tool("get_preferences", READ, "The user's availability rules (block/avoid/prefer windows).", _obj({}), t_rules),
    Tool("get_tasks", READ, "Open tasks with remaining work, free time before deadline and at-risk flags.",
         _obj({"include_closed": {"type": "boolean"}}), t_tasks),
    Tool("get_task", READ, "One task with its scheduled work blocks.", _obj({"task_id": {"type": "string"}}, ["task_id"]),
         t_task),
    Tool("propose_work_blocks", READ, "Candidate work-block plans for a task's remaining time before its deadline.",
         _obj({"task_id": {"type": "string"}}, ["task_id"]), t_work_blocks),
    Tool("get_attention_items", READ, "Displaced commitments needing reschedule and pending confirmations.",
         _obj({}), t_attention),

    Tool("check_commitment", PROPOSE,
         "Dry-run a new commitment: authority/conflict outcome, alternatives, options. Writes nothing.",
         _obj(COMMITMENT_FIELDS, ["title", "start"]), t_check),
    Tool("check_update", PROPOSE, "Dry-run a change to an existing commitment. Writes nothing.",
         _obj({"id": CID, "changes": CHANGES, "displace": COMMITMENT_FIELDS["displace"]}, ["id", "changes"]),
         t_check_update),

    Tool("create_commitment", WRITE,
         "Request creating a commitment. Returns needs_confirmation + token (ask the user), or rejected with "
         "alternatives. Never retry with a different authority level unless the user explicitly said so.",
         _obj(COMMITMENT_FIELDS, ["title", "start"]), _submit("create_commitment")),
    Tool("modify_commitment", WRITE, "Request changing a commitment (time, level, status, details).",
         _obj({"id": CID, "changes": CHANGES, "displace": COMMITMENT_FIELDS["displace"]}, ["id", "changes"]),
         _submit("update_commitment")),
    Tool("move_occurrence", WRITE, "Request changing ONE occurrence of a recurring commitment.",
         _obj({"id": CID, "occurrence_start": DT, "changes": CHANGES, "displace": COMMITMENT_FIELDS["displace"]},
              ["id", "occurrence_start", "changes"]), _submit("move_occurrence")),
    Tool("cancel_commitments", DELETE,
         "Request cancelling one or more commitments. More than one requires the user to type an exact phrase.",
         _obj({"ids": {"type": "array", "items": CID, "minItems": 1}}, ["ids"]), _submit("cancel_commitments")),
    Tool("skip_occurrence", DELETE, "Request skipping one occurrence of a recurring commitment.",
         _obj({"id": CID, "occurrence_start": DT}, ["id", "occurrence_start"]), _submit("skip_occurrence")),
    Tool("execute_force", WRITE,
         "FORCE: place a commitment regardless of authority, displacing everything overlapping. ONLY when the user "
         "explicitly asks to override everything. Always requires confirmation.",
         _obj(COMMITMENT_FIELDS, ["title", "start"]), _submit("force_commitment")),
    Tool("create_person", WRITE, "Request adding a person profile.",
         _obj({"name": {"type": "string"}, "aliases": {"type": "string"}, "relationship": {"type": "string"},
               "communication_style": {"type": "string"}, "preferences": {"type": "string"},
               "custom_instructions": {"type": "string"}, "notes": {"type": "string"}}, ["name"]),
         _submit("create_person")),
    Tool("update_person_profile", WRITE,
         "Request a profile update you noticed (e.g. prefers short meetings). Requires user approval.",
         _obj({"id": {"type": "string"}, "changes": _obj({
             "aliases": {"type": "string"}, "relationship": {"type": "string"}, "importance": {"type": "string"},
             "communication_style": {"type": "string"}, "preferences": {"type": "string"},
             "custom_instructions": {"type": "string"}, "notes": {"type": "string"}})}, ["id", "changes"]),
         _submit("update_person")),
    Tool("create_task", WRITE, "Add a task (does not occupy time). Tell the user you added it.",
         _obj({"title": {"type": "string"}, "deadline": DT, "estimated_duration_min": {"type": "integer", "minimum": 1},
               "notes": {"type": "string"}}, ["title", "deadline", "estimated_duration_min"]), t_create_task),
    Tool("modify_task", WRITE, "Change a task (title, deadline, estimate, status open/done/dropped).",
         _obj({"task_id": {"type": "string"}, "changes": _obj({
             "title": {"type": "string"}, "deadline": DT, "estimated_duration_min": {"type": "integer"},
             "status": {"type": "string", "enum": ["open", "done", "dropped"]}, "notes": {"type": "string"}})},
              ["task_id", "changes"]), t_update_task),
    Tool("delete_tasks", DELETE, "Request deleting tasks. More than one requires a typed phrase.",
         _obj({"ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, ["ids"]),
         _submit("delete_tasks")),

    Tool("confirm_pending", CONFIRM,
         "Execute a pending action after the user replied. The system checks the user's actual message; "
         "hedged or unclear replies are refused.", _obj({"token": {"type": "string"}}, ["token"]), t_confirm),
    Tool("reject_pending", CONFIRM, "Drop a pending action the user declined.",
         _obj({"token": {"type": "string"}}, ["token"]), t_reject),
]

BY_NAME = {t.name: t for t in TOOLS}

# The chat assistant gets a lean subset: every schema is sent on every model call, and free
# API tiers limit tokens per minute. MCP clients and scripts still get the full TOOLS list.
CHAT_TOOLS = [
    "find_person", "find_commitments", "get_schedule", "find_available_slots", "check_commitment",
    "create_commitment", "modify_commitment", "cancel_commitments", "get_tasks", "create_task",
    "propose_work_blocks", "confirm_pending", "reject_pending",
]


def _compact(schema: dict, top: bool = True) -> dict:
    """Drop what the model can do without: nested descriptions, empty required lists, strictness flags."""
    out = {}
    for k, v in schema.items():
        if k == "additionalProperties" or (k == "required" and not v):
            continue
        if k == "description" and not top:
            continue
        if k == "properties":
            v = {name: _compact(p, top=False) for name, p in v.items()}
        elif k == "items" and isinstance(v, dict):
            v = _compact(v, top=False)
        out[k] = v
    return out


def chat_schemas() -> list[dict]:
    return [{"type": "function", "function": {
        "name": t.name, "description": t.description.split(". ")[0].rstrip(".") + ".",
        "parameters": _compact(t.parameters)}} for t in (BY_NAME[n] for n in CHAT_TOOLS)]


def tool_catalog() -> list[dict]:
    return [{"name": t.name, "category": t.category, "description": t.description, "parameters": t.parameters}
            for t in TOOLS]


def call_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    """Run a tool; failures come back as structured errors, never exceptions."""
    tool = BY_NAME.get(name)
    if tool is None:
        return {"error": "UNKNOWN_TOOL", "message": f"No tool named {name}"}
    if not isinstance(args, dict):
        return {"error": "INVALID_ARGS", "message": "Arguments must be an object"}
    try:
        result = tool.handler(ctx, args)
        return result if isinstance(result, dict) else {"result": result}
    except ServiceError as e:
        return e.to_dict()
    except (KeyError, TypeError, ValueError) as e:
        return {"error": "INVALID_ARGS", "message": str(e)}
