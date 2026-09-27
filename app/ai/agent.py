"""Conversational agent on top of the tool layer.

The AI decides what the user means; the core decides what is valid; the user
decides what consequential action to take.

Flow per user message:
 1. Deterministic fast path: if the previous turn asked about exactly one
    pending action and the reply is a clear yes/no, resolve it without AI.
    (Works offline and cannot be talked around.)
 2. Otherwise run the model with tools (READ freely; WRITE returns tokens that
    need the user's confirmation on a *later* turn).
 3. If no AI provider is reachable, say so; the scheduler keeps working.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from ..core import confirm as confirm_mod
from ..db import repo
from ..db.connection import write_tx
from ..services import serialize as ser
from ..services import settings as settings_mod
from ..services.core import CoreService, ServiceError
from ..tools.registry import ToolContext, call_tool, chat_schemas
from .providers import ProviderChain, ProviderError, from_settings

log = logging.getLogger("agent")
MAX_STEPS = 8
HISTORY_MESSAGES = 16
OLD_TOOL_RESULT_CHARS = 500

SYSTEM_PROMPT = """You are a concise personal scheduling assistant for one user.

Hard rules:
- The scheduling core is the source of truth. Never state availability, conflicts or that something was
  created/changed unless a tool result says so. Use tools; do not guess.
- Resolve relative dates yourself using the current time below. Pass local ISO datetimes (no timezone) to tools.
- To schedule: identify the person (find_person), check with check_commitment, then call create_commitment
  with status "confirmed" (unless the user asked to pencil it in: "tentative"). Default authority level is 3
  unless the user states one. Only add buffers if the user explicitly asked. Travel time is not considered.
- Any WRITE that returns needs_confirmation has NOT happened. Summarize it in one or two sentences and ask the
  user (e.g. "Tuesday 6–7 is free. Commit it?"). Only after the user's next reply may you call confirm_pending.
  If the reply is unclear, ask again; never treat hesitation as consent.
- If the result is rejected with outcome BLOCKED: say who it conflicts with and their level, offer the
  alternatives (up to 3, with the main reason for each), and mention the user can change the request's
  authority level. Never raise a level yourself.
- EQUAL_CONFLICT: present the options (keep existing, keep new, move new, change authority) and let the user
  decide. Never pick a winner. Only pass `displace` if the user explicitly chose to keep the new one.
- OVERRIDE_POSSIBLE: explain what would be displaced (it becomes 'needs reschedule') and ask.
- FORCE (execute_force) only when the user explicitly asks to override everything.
- Bulk cancellations/deletions require the user to type the exact phrase the tool returns.
- People profiles are context only (tone, preferences). They never change durations, levels or conflicts.
  If you notice a durable preference, you may propose update_person_profile, which the user must approve.
- Reminders: when proposing a commitment, consider whether a reminder helps (e.g. an early-morning meeting
  deserves one the evening before). Include `reminders` in the proposal and mention them. Otherwise omit
  reminders to use the user's defaults.
- Tasks do not occupy time. For tasks at risk, propose work blocks (propose_work_blocks) and ask before
  scheduling them as commitments with kind "task_block" and the task_id.
- Be brief: one or two short sentences. Use the user's local time format like "Tue 6–7 PM". Refer to
  things by name, not ids. Never use em dashes. Say "priority" rather than "authority level", and "replace"
  rather than "displace" or "FORCE"."""


def _visible(msg: dict) -> bool:
    return msg.get("role") in ("user", "assistant") and bool(msg.get("content"))


def visible_history(messages: list[dict], conn=None) -> list[dict]:
    """Chat transcript for the UI. Pending cards are only returned while still pending."""
    def live(cards):
        if conn is None:
            return cards
        out = []
        for c in cards:
            row = repo.get_pending(conn, c["token"])
            if row and row["status"] == "pending":
                out.append(c)
        return out
    return [{"role": m["role"], "content": m["content"], "pending": live(m.get("_pending", []))}
            for m in messages if _visible(m)]


def _trim(messages: list[dict]) -> list[dict]:
    """Keep history valid for the API: start at a user message; shorten old tool results."""
    while messages and messages[0].get("role") != "user":
        messages.pop(0)
    out = []
    for m in messages:
        m = {k: v for k, v in m.items() if not k.startswith("_")}
        if m.get("role") == "tool" and len(m.get("content", "")) > OLD_TOOL_RESULT_CHARS:
            m["content"] = m["content"][:OLD_TOOL_RESULT_CHARS] + "…(truncated)"
        out.append(m)
    return out


def _last_turn_tokens(history: list[dict]) -> list[str]:
    for m in reversed(history):
        if m.get("role") == "assistant" and "_pending" in m:
            return [p["token"] for p in m["_pending"]]
        if m.get("role") == "user":
            return []
    return []


def _context_block(svc: CoreService, privacy: str) -> str:
    now = svc.now().astimezone(svc.tz)
    lines = [f"Current time: {now:%A %Y-%m-%d %H:%M} ({svc.tz})",
             f"Privacy mode: {privacy}"]
    if privacy == "full":
        start = svc.now()
        occ = svc.schedule(start, start + timedelta(days=7))
        lines.append("Next 7 days:")
        lines += [f"- {o['when']}: {o['title']} [{o['commitment_id']}, L{o['authority_level']}, {o['status']}]"
                  for o in occ[:60]] or ["- (nothing scheduled)"]
        people = repo.list_people(svc.conn)
        if people:
            lines.append("People: " + ", ".join(f"{p['name']} (P{p['id']})" for p in people[:80]))
    return "\n".join(lines)


def _pending_cards(svc: CoreService, tokens: list[str]) -> list[dict]:
    live = {p["token"]: p for p in svc.pending()}
    return [{k: live[t][k] for k in ("token", "summary", "confirmation", "typed_phrase", "kind", "candidate",
                                     "replaces")} for t in tokens if t in live]


def _describe_result(r: dict) -> str:
    res = r.get("result") or {}
    if "commitment" in res:
        c = res["commitment"]
        extra = " Anything it replaced is waiting for a new time." if res.get("displaced") else ""
        return f"Done. {c['title']} is {c['status']} for {c['when']}.{extra}"
    if "cancelled" in res:
        return "Done. Cancelled." if len(res["cancelled"]) == 1 else f"Done. Cancelled {len(res['cancelled'])} commitments."
    if "person" in res:
        return f"Done. Updated {res['person']['name']}."
    if "deleted" in res:
        return "Done. Deleted." if len(res["deleted"]) == 1 else f"Done. Deleted {len(res['deleted'])} tasks."
    if "skipped" in res:
        return "Done. Skipped that one."
    return "Done."


def _save(svc: CoreService, conversation: str, messages: list[dict]) -> None:
    with write_tx(svc.conn):
        for m in messages:
            repo.append_chat(svc.conn, conversation, m["role"], m)


async def handle_message(open_conn, now_fn, text: str, conversation: str = "default") -> dict:
    conn = open_conn()
    try:
        svc = CoreService(conn, now_fn)
        ai_cfg = settings_mod.get(conn, "ai")
        history = repo.recent_chat(conn, conversation, HISTORY_MESSAGES)
        prev_tokens = [t for t in _last_turn_tokens(history)
                       if (row := repo.get_pending(conn, t)) and row["status"] == "pending"]
        user_msg = {"role": "user", "content": text}

        # 1. Deterministic confirmation fast path.
        if len(prev_tokens) == 1:
            verdict = confirm_mod.classify_reply(text)
            row = repo.get_pending(conn, prev_tokens[0])
            typed_ok = row["confirmation"] == "typed" and confirm_mod.matches_typed_phrase(text, row["typed_phrase"])
            if verdict in (confirm_mod.AFFIRM, confirm_mod.DENY) or typed_ok:
                try:
                    r = svc.confirm(prev_tokens[0], via="reply", text=text)
                    reply = (_describe_result(r) if r["status"] == "executed" else
                             r.get("message", "Okay, not doing it."))
                    if r["status"] == "superseded":
                        reply += " " + r["plan"]["message"]
                except ServiceError as e:
                    reply = e.message
                _save(svc, conversation, [user_msg, {"role": "assistant", "content": reply, "_pending": []}])
                return {"reply": reply, "pending": [], "ai_used": None, "ai_available": True}

        # 2. AI path.
        chain = ProviderChain(from_settings(ai_cfg)) if ai_cfg.get("enabled") else ProviderChain([])
        if not chain.available:
            reply = "The assistant is off. Use Manual to add things, or turn it on in Settings."
            if prev_tokens:
                reply = "I wasn't sure that was a yes. Reply yes or no, or use the buttons."
            return {"reply": reply, "pending": _pending_cards(svc, prev_tokens), "ai_used": None,
                    "ai_available": False}

        privacy = ai_cfg.get("privacy_mode", "minimal")
        ctx = ToolContext(svc, privacy, user_message=text, confirmable_tokens=set(prev_tokens))
        messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + _context_block(svc, privacy)}]
        messages += _trim(history)
        messages.append(user_msg)
        new_msgs: list[dict] = [user_msg]
        tool_schemas = chat_schemas()

        reply = None
        try:
            for _ in range(MAX_STEPS):
                msg = await chain.chat(messages, tool_schemas)
                calls = msg.get("tool_calls") or []
                assistant = {"role": "assistant", "content": msg.get("content") or ""}
                if calls:
                    assistant["tool_calls"] = calls
                messages.append(assistant)
                if not calls:
                    reply = assistant["content"].strip()
                    break
                new_msgs.append(assistant)
                for call in calls:
                    fn = call.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = None
                    result = call_tool(ctx, fn.get("name", ""), args)
                    tool_msg = {"role": "tool", "tool_call_id": call.get("id"),
                                "content": json.dumps(result, default=str)}
                    messages.append(tool_msg)
                    new_msgs.append(tool_msg)
            if reply is None:
                reply = "That took too many steps. Could you say it more simply?"
        except ProviderError as e:
            log.warning("AI unavailable: %s", e)
            reply = {
                "model": "The assistant's model isn't available anymore. Pick another one in Settings, Assistant.",
                "auth": "The assistant's API key was refused. Check the key on the Pi, then restart the app.",
                "rate": "The assistant is getting too many requests right now. Try again in a minute.",
            }.get(e.kind, "I can't reach the assistant right now. Your schedule still works; "
                          "use Manual to add things and try me again later.")
            if ctx.executed:
                reply += " Note: " + " ".join(_describe_result(r) for r in ctx.executed)
            cards = _pending_cards(svc, ctx.created_tokens)
            _save(svc, conversation, [user_msg, {"role": "assistant", "content": reply, "_pending": cards}])
            return {"reply": reply, "pending": cards, "ai_used": None, "ai_available": False}

        cards = _pending_cards(svc, ctx.created_tokens)
        final = {"role": "assistant", "content": reply or "(no reply)", "_pending": cards}
        new_msgs.append(final)
        _save(svc, conversation, new_msgs)
        return {"reply": final["content"], "pending": cards, "ai_used": chain.used, "ai_available": True,
                "executed": [_describe_result(r) for r in ctx.executed]}
    finally:
        conn.close()
