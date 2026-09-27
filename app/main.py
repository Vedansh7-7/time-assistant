"""FastAPI application: REST API + web UI + MCP endpoint.

Every route is a thin adapter over app.services; no route contains scheduling logic.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import __version__
from . import assets
from . import config as config_mod
from .ai import agent as agent_mod
from .ai import providers as providers_mod
from .db import repo
from .db.connection import connect, migrate, utcnow, write_tx
from .mcp import handle_mcp
from .public_app import add_error_handlers, public_router
from .services import backup as backup_mod
from .services import mailer
from .services import public as public_mod
from .services import queries, reminders
from .services import serialize as ser
from .services import settings as settings_mod
from .services.core import CoreService, ServiceError
from .tools.registry import tool_catalog

STATIC = Path(__file__).resolve().parent / "static"
log = logging.getLogger("app")


def create_app(cfg: config_mod.Config | None = None, now_fn=utcnow) -> FastAPI:
    cfg = cfg or config_mod.load()
    state: dict[str, Any] = {"started": time.time(), "backup": {}}

    def open_conn():
        return connect(cfg.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = open_conn()
        migrate(conn)
        conn.close()
        tasks = []
        if cfg.run_background:
            tasks.append(asyncio.create_task(reminders.run_loop(open_conn, now_fn)))
            tasks.append(asyncio.create_task(backup_mod.run_loop(open_conn, cfg.backup_dir, state["backup"])))
            tasks.append(asyncio.create_task(mailer.run_loop(open_conn)))
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="Personal Time Assistant", version=__version__, lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    def get_svc():
        conn = open_conn()
        try:
            yield CoreService(conn, now_fn)
        finally:
            conn.close()

    add_error_handlers(app)
    app.include_router(public_router(open_conn, now_fn))

    # ------------------------------------------------------------ static / health
    @app.get("/", include_in_schema=False)
    def index():
        return HTMLResponse(assets.owner_index(), headers=assets.REVALIDATE)

    @app.get("/static/v/{build}/{path:path}", include_in_schema=False)
    def static_versioned(build: str, path: str):
        f = assets.resolve(STATIC, path)
        if f is None:
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        # Only the current build is immutable; an old tab asking for an old build gets today's file, revalidated.
        return FileResponse(f, headers=assets.IMMUTABLE if build == assets.build_id() else assets.REVALIDATE)

    @app.get("/static/fonts/{name}", include_in_schema=False)
    def font(name: str):
        f = (STATIC / "fonts" / name).resolve()
        if (STATIC / "fonts").resolve() not in f.parents or not f.is_file():
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/static/{path:path}", include_in_schema=False)
    def static(path: str):
        f = (STATIC / path).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        return FileResponse(f, headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/health")
    def health(svc: CoreService = Depends(get_svc)):
        svc.conn.execute("SELECT 1").fetchone()
        return {"ok": True, "version": __version__, "time": ser.local_iso(svc.now(), svc.tz)}

    # ------------------------------------------------------------ reads
    @app.get("/api/dashboard")
    def dashboard(svc: CoreService = Depends(get_svc)):
        return queries.dashboard(svc)

    @app.get("/api/schedule")
    def schedule(start: datetime, end: datetime, include_inactive: bool = False,
                 svc: CoreService = Depends(get_svc)):
        s, e = svc._dt(start), svc._dt(end)
        if e <= s or e - s > timedelta(days=62):
            raise ServiceError("INVALID_RANGE", "Range must be positive and at most 62 days")
        return {"occurrences": svc.schedule(s, e, include_inactive)}

    @app.get("/api/day/{d}")
    def day(d: date, svc: CoreService = Depends(get_svc)):
        return queries.day(svc, d)

    @app.get("/api/commitments")
    def commitments(status: list[str] = Query(default=[]), person: str | None = None, task: str | None = None,
                    svc: CoreService = Depends(get_svc)):
        pid = ser.parse_id(person, "P") if person else None
        tid = ser.parse_id(task, "T") if task else None
        people = svc.people_names()
        return [ser.commitment(c, svc.tz, people)
                for c in repo.list_commitments(svc.conn, statuses=status or None, person_id=pid, task_id=tid)]

    @app.get("/api/commitments/{cid}")
    def commitment(cid: str, svc: CoreService = Depends(get_svc)):
        c = svc._require_commitment(cid)
        d = ser.commitment(c, svc.tz, svc.people_names())
        d["reminder_preview"] = reminders.preview(svc.conn, svc.tz, c, svc.now())
        return d

    @app.post("/api/slots")
    def slots(body: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return {"slots": queries.find_available_slots(
            svc, int(body.get("duration_min", 60)), body.get("start"), body.get("end"), body.get("kind", "meeting"),
            int(body.get("buffer_before_min", 0)), int(body.get("buffer_after_min", 0)), body.get("near"),
            body.get("limit"), body.get("exclude_id"))}

    # ------------------------------------------------------------ guarded actions
    @app.post("/api/check/{kind}")
    def check(kind: str, payload: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return svc.check(kind, payload, "ui")

    @app.post("/api/actions/{kind}")
    def action(kind: str, payload: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return svc.submit(kind, payload, "ui")

    @app.post("/api/commitments")
    def create_commitment(payload: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return svc.submit("create_commitment", payload, "ui")

    @app.patch("/api/commitments/{cid}")
    def update_commitment(cid: str, body: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return svc.submit("update_commitment", {"id": cid, "changes": body.get("changes", {}),
                                                "displace": body.get("displace", [])}, "ui")

    @app.post("/api/commitments/{cid}/cancel")
    def cancel_commitment(cid: str, svc: CoreService = Depends(get_svc)):
        return svc.submit("cancel_commitments", {"ids": [cid]}, "ui")

    @app.get("/api/pending")
    def pending(svc: CoreService = Depends(get_svc)):
        return svc.pending()

    @app.post("/api/pending/{token}/confirm")
    def confirm(token: str, body: dict = Body(default={}), svc: CoreService = Depends(get_svc)):
        via = body.get("via", "button")
        if via == "reply":
            raise ServiceError("INVALID", "Chat replies are confirmed through the chat endpoint")
        return svc.confirm(token, via=via, text=body.get("text"))

    @app.post("/api/pending/{token}/reject")
    def reject(token: str, svc: CoreService = Depends(get_svc)):
        return svc.reject(token)

    # ------------------------------------------------------------ people
    @app.get("/api/people")
    def people(svc: CoreService = Depends(get_svc)):
        return [ser.person(p) for p in repo.list_people(svc.conn)]

    @app.get("/api/people/{pid}")
    def person(pid: str, svc: CoreService = Depends(get_svc)):
        p = repo.get_person(svc.conn, ser.parse_id(pid, "P"))
        if p is None:
            raise ServiceError("NOT_FOUND", "Person not found", 404)
        out = ser.person(p)
        now = svc.now()
        out["upcoming"] = [ser.commitment(c, svc.tz) for c in repo.list_commitments(
            svc.conn, now, now + timedelta(days=60), ["tentative", "confirmed", "needs_reschedule"], person_id=p["id"])]
        return out

    @app.post("/api/people")
    def create_person(body: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return svc.upsert_person_direct(body)

    @app.patch("/api/people/{pid}")
    def update_person(pid: str, body: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return svc.upsert_person_direct(body, pid)

    @app.delete("/api/people/{pid}")
    def delete_person(pid: str, svc: CoreService = Depends(get_svc)):
        with write_tx(svc.conn):
            repo.delete_person(svc.conn, ser.parse_id(pid, "P"))
        return {"ok": True}

    # ------------------------------------------------------------ tasks
    @app.get("/api/tasks")
    def tasks(include_closed: bool = False, svc: CoreService = Depends(get_svc)):
        return queries.tasks(svc, include_closed)

    @app.get("/api/tasks/{tid}")
    def task(tid: str, svc: CoreService = Depends(get_svc)):
        return queries.task_detail(svc, tid)

    @app.post("/api/tasks")
    def create_task(body: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return svc.create_task(body)

    @app.patch("/api/tasks/{tid}")
    def update_task(tid: str, body: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return svc.update_task(tid, body)

    @app.get("/api/tasks/{tid}/work-blocks")
    def work_blocks(tid: str, svc: CoreService = Depends(get_svc)):
        return queries.work_block_options(svc, tid)

    # ------------------------------------------------------------ availability rules & settings
    @app.get("/api/rules")
    def rules(svc: CoreService = Depends(get_svc)):
        return [ser.rule(r) for r in repo.list_rules(svc.conn)]

    @app.post("/api/rules")
    def create_rule(body: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return _save_rule(svc, body, None)

    @app.put("/api/rules/{rid}")
    def update_rule(rid: str, body: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return _save_rule(svc, body, ser.parse_id(rid, "R"))

    @app.delete("/api/rules/{rid}")
    def delete_rule(rid: str, svc: CoreService = Depends(get_svc)):
        with write_tx(svc.conn):
            repo.delete_rule(svc.conn, ser.parse_id(rid, "R"))
        return {"ok": True}

    @app.get("/api/settings")
    def get_settings(svc: CoreService = Depends(get_svc)):
        return settings_mod.get_all(svc.conn)

    @app.put("/api/settings/{key}")
    def put_setting(key: str, value: Any = Body(...), svc: CoreService = Depends(get_svc)):
        with write_tx(svc.conn):
            settings_mod.put(svc.conn, key, value)
        return settings_mod.get(svc.conn, key)

    # ------------------------------------------------------------ notifications, system, backup
    @app.get("/api/notifications")
    def notifications(svc: CoreService = Depends(get_svc)):
        return repo.active_notifications(svc.conn)

    @app.post("/api/notifications/dismiss")
    def dismiss(body: dict = Body(...), svc: CoreService = Depends(get_svc)):
        with write_tx(svc.conn):
            repo.dismiss_notification(svc.conn, body["occurrence_key"], body["fire_at"])
        return {"ok": True}

    @app.get("/api/system")
    async def system(svc: CoreService = Depends(get_svc)):
        ai_cfg = settings_mod.get(svc.conn, "ai")
        db_size = cfg.db_path.stat().st_size if cfg.db_path.exists() else 0
        return {
            "version": __version__, "uptime_s": int(time.time() - state["started"]),
            "timezone": str(svc.tz), "db_path": str(cfg.db_path), "db_bytes": db_size,
            "schema_version": svc.conn.execute("PRAGMA user_version").fetchone()[0],
            "ai": {"enabled": ai_cfg["enabled"], "privacy_mode": ai_cfg["privacy_mode"],
                   "providers": await providers_mod.status(ai_cfg)},
            "backup": {"dir": str(cfg.backup_dir), "last": state["backup"].get("last_result"),
                       "last_error": state["backup"].get("last_error"),
                       "files": backup_mod.list_backups(cfg.backup_dir)[:10]},
        }

    @app.post("/api/backup/run")
    def run_backup(svc: CoreService = Depends(get_svc)):
        b = settings_mod.get(svc.conn, "backup")
        try:
            result = backup_mod.snapshot(svc.conn, cfg.backup_dir, int(b["keep"]), b["target_dir"])
        except RuntimeError as e:
            state["backup"]["last_error"] = str(e)
            raise ServiceError("BACKUP_FAILED", str(e), 500)
        state["backup"].update(last_result=result, last_error=None)
        return result

    # ------------------------------------------------------------ public booking requests (owner side)
    @app.get("/api/requests/count")
    def requests_count(svc: CoreService = Depends(get_svc)):
        n = svc.conn.execute("SELECT COUNT(*) FROM public_requests WHERE status = 'requested'").fetchone()[0]
        return {"open": n}

    @app.get("/api/requests")
    def requests_list(status: list[str] = Query(default=[]), svc: CoreService = Depends(get_svc)):
        return public_mod.owner_list(svc, status or None)

    @app.post("/api/requests/{rid}/decide")
    def requests_decide(rid: str, body: dict = Body(...), svc: CoreService = Depends(get_svc)):
        return public_mod.decide(svc, rid, body.get("decision", ""), body.get("reason"),
                                 bool(body.get("share_reason")))

    @app.get("/api/outbox")
    def outbox(svc: CoreService = Depends(get_svc)):
        rows = svc.conn.execute("SELECT id, to_addr, subject, status, attempts, last_error, created_at, sent_at "
                                "FROM outbox ORDER BY id DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ AI + tools + MCP
    @app.get("/api/tools")
    def tools():
        return tool_catalog()

    @app.post("/api/chat")
    async def chat(body: dict = Body(...)):
        text = (body.get("message") or "").strip()
        if not text:
            raise ServiceError("INVALID", "Empty message")
        return await agent_mod.handle_message(open_conn, now_fn, text, body.get("conversation", "default"))

    @app.get("/api/chat/history")
    def chat_history(conversation: str = "default", svc: CoreService = Depends(get_svc)):
        return agent_mod.visible_history(repo.recent_chat(svc.conn, conversation, 60), svc.conn)

    @app.delete("/api/chat")
    def chat_clear(conversation: str = "default", svc: CoreService = Depends(get_svc)):
        with write_tx(svc.conn):
            repo.clear_chat(svc.conn, conversation)
        return {"ok": True}

    @app.post("/mcp")
    async def mcp(request: Request):
        return await handle_mcp(request, open_conn, now_fn)

    return app


def _save_rule(svc: CoreService, body: dict, rid: int | None) -> dict:
    from .core.model import AvailabilityRule, RuleKind

    def minutes(v) -> int:
        if isinstance(v, int):
            return v
        h, m = str(v).split(":")
        return int(h) * 60 + int(m)

    try:
        rule = AvailabilityRule(
            id=rid, kind=RuleKind(body["kind"]), start_minute=minutes(body["start"]), end_minute=minutes(body["end"]),
            weekdays=tuple(sorted({int(d) for d in body.get("weekdays") or []})),
            applies_to=body.get("applies_to") or None, label=body.get("label", ""), weight=int(body.get("weight", 1)),
        )
    except (KeyError, ValueError) as e:
        raise ServiceError("INVALID", f"Invalid rule: {e}")
    if not 0 <= rule.start_minute < rule.end_minute <= 1440 or any(not 0 <= d <= 6 for d in rule.weekdays):
        raise ServiceError("INVALID", "Rule times must be within the day, end after start; weekdays 0–6")
    with write_tx(svc.conn):
        new_id = repo.upsert_rule(svc.conn, rule)
    return ser.rule(repo.get_rule(svc.conn, new_id))


app = create_app()
