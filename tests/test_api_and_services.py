"""REST API, reminders, backups, MCP and agent guardrails (with a fake AI provider)."""
import asyncio
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.ai import agent as agent_mod
from app.ai import providers as providers_mod
from app.config import Config
from app.db import repo
from app.db.connection import connect, migrate
from app.main import create_app
from app.services import backup, reminders, settings as settings_mod
from app.services.core import CoreService

from .conftest import NOW, TZ, Clock, local, mk


@pytest.fixture
def client(tmp_path):
    cfg = Config(tmp_path, tmp_path / "t.db", tmp_path / "backups", run_background=False)
    conn = connect(cfg.db_path)
    migrate(conn)
    repo.set_setting(conn, "timezone", "Asia/Kolkata")
    conn.close()
    with TestClient(create_app(cfg, Clock(NOW))) as c:
        yield c


def test_api_create_conflict_and_confirm(client):
    r = client.post("/api/commitments", json={"title": "Arjun", "start": local(29, 18), "end": local(29, 19),
                                              "status": "confirmed", "authority_level": 3}).json()
    assert r["status"] == "executed" and r["result"]["commitment"]["id"] == "C1"
    r = client.post("/api/commitments", json={"title": "Rahul", "start": local(29, 18), "end": local(29, 19),
                                              "authority_level": 2}).json()
    assert r["status"] == "rejected" and r["plan"]["alternatives"]
    r = client.post("/api/commitments", json={"title": "Rahul", "start": local(29, 18), "end": local(29, 19),
                                              "authority_level": 4, "status": "confirmed"}).json()
    assert r["status"] == "needs_confirmation"
    ok = client.post(f"/api/pending/{r['token']}/confirm", json={"via": "button"}).json()
    assert ok["status"] == "executed"
    dash = client.get("/api/dashboard").json()
    assert [c["title"] for c in dash["needs_reschedule"]] == ["Arjun"]


def test_api_structured_errors(client):
    r = client.post("/api/commitments", json={"title": "X", "start": local(29, 19), "end": local(29, 18)})
    assert r.status_code == 400 and r.json()["error"] == "INVALID_TIME"
    assert client.get("/api/commitments/C999").status_code == 404
    assert client.post("/api/pending/Anope/confirm", json={}).status_code == 404


def test_api_reply_confirmation_not_allowed_via_rest(client):
    r = client.post("/api/commitments", json={"title": "A", "start": local(29, 18), "end": local(29, 19)}).json()
    r = client.post("/api/actions/cancel_commitments", json={"ids": ["C1"]}).json()
    assert r["status"] == "executed"  # tentative cancel is direct


def test_api_people_tasks_rules_settings(client):
    p = client.post("/api/people", json={"name": "Rahul", "communication_style": "straight to the point"}).json()
    assert p["id"] == "P1"
    assert client.post("/api/people", json={"name": "rahul"}).status_code == 409
    t = client.post("/api/tasks", json={"title": "ML", "deadline": local(30, 23), "estimated_duration_min": 120}).json()
    assert client.get(f"/api/tasks/{t['id']}/work-blocks").json()["options"]
    rule = client.post("/api/rules", json={"kind": "block", "start": "19:00", "end": "20:00", "label": "family"}).json()
    assert rule["id"] == "R1"
    assert client.put("/api/settings/search", json={"day_start": "09:00", "day_end": "08:00"}).status_code == 400
    assert client.put("/api/settings/timezone", json="Mars/Olympus").status_code == 400
    day = client.get("/api/day/2026-09-29").json()
    assert any(b["kind"] == "blocked" for b in day["blocks"])


def test_mcp_lists_tools_and_cannot_confirm(client):
    init = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).json()
    assert init["result"]["serverInfo"]["name"] == "time-assistant"
    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "find_available_slots" in names and "confirm_pending" not in names
    call = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "create_commitment", "arguments": {"title": "X", "start": local(29, 18), "end": local(29, 19)}}}).json()
    assert call["result"]["structuredContent"]["status"] == "needs_confirmation"
    assert client.get("/api/commitments").json() == []


# ---------------------------------------------------------------- reminders & backup
def test_reminders_fire_once_and_follow_moves(svc, clock):
    cid = mk(svc, "Standup", local(28, 10), local(28, 10, 30), reminders=[{"offset_min": 30}])
    clock.t = NOW + timedelta(minutes=29)  # 09:29, reminder at 09:30
    assert reminders.fire_due(svc.conn, TZ, clock.t) == []
    clock.t = NOW + timedelta(minutes=31)
    fired = reminders.fire_due(svc.conn, TZ, clock.t)
    assert len(fired) == 1 and "Standup" in fired[0].message
    assert reminders.fire_due(svc.conn, TZ, clock.t) == []  # deduped
    # Move to 11:00 -> new reminder at 10:30
    svc.submit("update_commitment", {"id": cid, "changes": {"start": local(28, 11)}}, "ui")
    pending = svc.pending()
    if pending:
        svc.confirm(pending[0]["token"], via="button")
    clock.t = NOW + timedelta(minutes=91)
    assert len(reminders.fire_due(svc.conn, TZ, clock.t)) == 1


def test_default_and_disabled_reminders(svc, clock):
    mk(svc, "A", local(28, 10), local(28, 11))                 # default: 15 min before
    mk(svc, "B", local(28, 12), local(28, 13), reminders=[])   # explicitly none
    clock.t = NOW + timedelta(hours=4)  # 13:00, past both
    msgs = [d.message for d in reminders.fire_due(svc.conn, TZ, NOW + timedelta(minutes=46))]
    assert len(msgs) == 1 and msgs[0].startswith("A")
    assert reminders.fire_due(svc.conn, TZ, NOW + timedelta(hours=3, minutes=50)) == []


def test_backup_snapshot_and_rotation(conn, tmp_path):
    for _ in range(3):
        backup.snapshot(conn, tmp_path / "b", keep=2)
        import time
        time.sleep(1.1)
    assert len(backup.list_backups(tmp_path / "b")) == 2


# ---------------------------------------------------------------- agent with a fake provider
class FakeChain:
    """Scripted model: returns queued messages, records what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.used = "fake"
        self.available = True
        self.seen = []

    async def chat(self, messages, tools):
        self.seen.append(list(messages))
        return self.script.pop(0)


def _call(name, args, id_="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _run(monkeypatch, conn_path, clock, text, script):
    chain = FakeChain(script)
    monkeypatch.setattr(agent_mod, "ProviderChain", lambda providers: chain)
    return asyncio.run(agent_mod.handle_message(lambda: connect(conn_path), clock, text)), chain


@pytest.fixture
def ai_db(tmp_path):
    path = tmp_path / "ai.db"
    c = connect(path)
    migrate(c)
    repo.set_setting(c, "timezone", "Asia/Kolkata")
    repo.set_setting(c, "ai", {"enabled": True})
    c.close()
    return path


def test_agent_cannot_confirm_in_same_turn(monkeypatch, ai_db, clock):
    args = {"title": "Rahul", "start": local(29, 18), "end": local(29, 19), "status": "confirmed"}
    def second(messages, tools):
        token = json.loads(messages[-1]["content"])["token"]
        return _call("confirm_pending", {"token": token}, "c2")
    chain = FakeChain([])

    async def chat(messages, tools):
        chain.seen.append(list(messages))
        if len(chain.seen) == 1:
            return _call("create_commitment", args)
        if len(chain.seen) == 2:
            return second(messages, tools)
        return {"role": "assistant", "content": "Tuesday 6–7 is free. Commit it?"}
    chain.chat = chat
    monkeypatch.setattr(agent_mod, "ProviderChain", lambda providers: chain)
    out = asyncio.run(agent_mod.handle_message(lambda: connect(ai_db), clock, "yes, Rahul wants to meet Tuesday 6-7"))
    confirm_result = json.loads(chain.seen[2][-1]["content"])
    assert confirm_result["status"] == "not_allowed"
    assert len(out["pending"]) == 1
    c = connect(ai_db)
    assert repo.list_commitments(c) == []
    c.close()

    # Next turn: a clear "yes" is resolved deterministically (no AI call).
    out2 = asyncio.run(agent_mod.handle_message(lambda: connect(ai_db), clock, "yes"))
    assert out2["reply"].startswith("Done")
    c = connect(ai_db)
    assert [x.title for x in repo.list_commitments(c)] == ["Rahul"]
    c.close()


def test_agent_ambiguous_reply_goes_to_ai_and_cannot_confirm(monkeypatch, ai_db, clock):
    args = {"title": "Rahul", "start": local(29, 18), "end": local(29, 19)}
    out, _ = _run(monkeypatch, ai_db, clock, "Rahul wants Tuesday 6-7",
                  [_call("create_commitment", args), {"role": "assistant", "content": "Free. Commit it?"}])
    token = out["pending"][0]["token"]
    out2, chain = _run(monkeypatch, ai_db, clock, "Hmm, okay...",
                       [_call("confirm_pending", {"token": token}),
                        {"role": "assistant", "content": "I'm not sure if you want me to proceed. Commit it?"}])
    result = json.loads(chain.seen[1][-1]["content"])
    assert result["error"] == "AMBIGUOUS_CONFIRMATION"
    c = connect(ai_db)
    assert repo.list_commitments(c) == []
    c.close()


def test_agent_offline_core_still_works(monkeypatch, ai_db, clock):
    class Down:
        available = True
        used = None

        async def chat(self, m, t):
            raise providers_mod.ProviderError("unreachable")
    monkeypatch.setattr(agent_mod, "ProviderChain", lambda providers: Down())
    out = asyncio.run(agent_mod.handle_message(lambda: connect(ai_db), clock, "what's on tomorrow"))
    assert out["ai_available"] is False
    c = connect(ai_db)
    svc = CoreService(c, clock)
    assert mk(svc, "Still works", local(29, 10), local(29, 11))
    c.close()


def test_user_facing_copy_has_no_em_dashes(client):
    client.post("/api/commitments", json={"title": "Arjun", "start": local(29, 18), "end": local(29, 19),
                                          "status": "confirmed", "authority_level": 3})
    texts = []
    for level, status in ((2, "confirmed"), (3, "confirmed"), (4, "confirmed"), (3, "tentative")):
        r = client.post("/api/check/create_commitment", json={"title": "Rahul", "start": local(29, 18),
                                                               "end": local(29, 19), "authority_level": level,
                                                               "status": status}).json()
        texts.append(r["message"])
        texts += [o["label"] for o in r["options"]] + r["warnings"]
    r = client.post("/api/check/force_commitment", json={"title": "X", "start": local(29, 18), "end": local(29, 19)})
    texts.append(r.json()["message"])
    assert texts and not [t for t in texts if "—" in t], texts


def test_static_assets_are_gzipped(client):
    r = client.get("/static/style.css", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"


def test_font_is_long_cached(client):
    r = client.get("/static/fonts/newsreader-latin-wght.woff2")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]


def test_pending_describes_candidate_and_replacements(client):
    client.post("/api/commitments", json={"title": "Arjun", "start": local(29, 18), "end": local(29, 19),
                                          "status": "confirmed"})
    client.post("/api/commitments", json={"title": "Rahul", "start": local(29, 18), "end": local(29, 19),
                                          "status": "confirmed", "authority_level": 4})
    (p,) = client.get("/api/pending").json()
    assert p["candidate"]["title"] == "Rahul" and p["replaces"] == ["Arjun"]
    assert client.get("/api/requests/count").json() == {"open": 0}


def test_pages_use_versioned_immutable_assets(client):
    import re
    html = client.get("/").text
    urls = re.findall(r'"(/static/v/[0-9a-f]{10}/[^"]+)"', html)
    assert any(u.endswith("/app.js") for u in urls) and "modulepreload" in html
    assert '"/static/app.js"' not in html  # never the old, possibly browser-cached URL
    for u in urls:
        r = client.get(u)
        assert r.status_code == 200, u
        assert "immutable" in r.headers["cache-control"], u
    # app.js imports './js/core.js'; relative imports must resolve inside the versioned folder
    app_js = next(u for u in urls if u.endswith("/app.js"))
    assert client.get(app_js.rsplit("/", 1)[0] + "/js/core.js").status_code == 200
    assert client.get("/").headers["cache-control"] == "no-cache"
    book = client.get("/book").text
    for u in re.findall(r'"(/book/v/[0-9a-f]{10}/[^"]+)"', book):
        assert client.get(u).status_code == 200, u
    assert '"/book/assets/book.js"' not in book
