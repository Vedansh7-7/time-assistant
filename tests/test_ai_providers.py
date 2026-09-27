"""Provider errors are classified and explained; models can be listed and providers tested.

A tiny local HTTP server imitates an OpenAI-compatible API (like Groq)."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.ai import agent as agent_mod
from app.ai.providers import Provider, ProviderChain, ProviderError
from app.db import repo
from app.db.connection import connect, migrate

from .conftest import Clock, NOW


class FakeAPI(BaseHTTPRequestHandler):
    good_model = "openai/gpt-oss-120b"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer good-key":
            return self._send(401, {"error": {"code": "invalid_api_key", "message": "Invalid API Key"}})
        self._send(200, {"data": [{"id": self.good_model}, {"id": "llama-3.1-8b-instant"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != "Bearer good-key":
            return self._send(401, {"error": {"code": "invalid_api_key", "message": "Invalid API Key"}})
        if body["model"] != self.good_model:
            return self._send(404, {"error": {"code": "model_not_found", "type": "invalid_request_error",
                                              "message": f"The model `{body['model']}` does not exist"}})
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": "OK"}}]})


@pytest.fixture(scope="module")
def api():
    srv = HTTPServer(("127.0.0.1", 0), FakeAPI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("TEST_GOOD_KEY", "good-key")
    monkeypatch.setenv("TEST_BAD_KEY", "bad-key")


def prov(api, model="openai/gpt-oss-120b", env="TEST_GOOD_KEY"):
    return Provider("groq", api, model, env)


def test_errors_are_classified(api, key):
    with pytest.raises(ProviderError) as e:
        asyncio.run(prov(api, model="llama-3.3-70b-versatile").chat([{"role": "user", "content": "hi"}], []))
    assert e.value.kind == "model"
    with pytest.raises(ProviderError) as e:
        asyncio.run(prov(api, env="TEST_BAD_KEY").chat([{"role": "user", "content": "hi"}], []))
    assert e.value.kind == "auth"
    with pytest.raises(ProviderError) as e:
        asyncio.run(Provider("x", "http://127.0.0.1:9/v1", "m").chat([], []))
    assert e.value.kind == "unreachable"


def test_chain_reports_the_actionable_failure(api, key):
    chain = ProviderChain([prov(api, model="gone"), Provider("local", "http://127.0.0.1:9/v1", "m")])
    with pytest.raises(ProviderError) as e:
        asyncio.run(chain.chat([], []))
    assert e.value.kind == "model"  # not "unreachable" from the second provider


def test_list_models_and_ping(api, key):
    assert asyncio.run(prov(api).list_models()) == ["llama-3.1-8b-instant", "openai/gpt-oss-120b"]
    assert asyncio.run(prov(api).ping())["ok"] is True
    bad = asyncio.run(prov(api, model="llama-3.3-70b-versatile").ping())
    assert bad["ok"] is False and bad["kind"] == "model"
    missing = asyncio.run(prov(api, env="TEST_UNSET_VAR").ping())
    assert missing["kind"] == "config" and "TEST_UNSET_VAR" in missing["error"]


def test_chat_explains_a_retired_model(api, key, tmp_path):
    path = tmp_path / "ai.db"
    c = connect(path)
    migrate(c)
    repo.set_setting(c, "timezone", "Asia/Kolkata")
    repo.set_setting(c, "ai", {"enabled": True, "providers": [
        {"name": "groq", "enabled": True, "base_url": api, "model": "llama-3.3-70b-versatile",
         "api_key_env": "TEST_GOOD_KEY"}]})
    c.close()
    out = asyncio.run(agent_mod.handle_message(lambda: connect(path), Clock(NOW), "what's on today"))
    assert out["ai_available"] is False
    assert "model" in out["reply"] and "Settings" in out["reply"]


def test_models_and_test_endpoints(api, key, tmp_path):
    from fastapi.testclient import TestClient
    from app.config import Config
    from app.main import create_app
    cfg = Config(tmp_path, tmp_path / "t.db", tmp_path / "b", run_background=False)
    with TestClient(create_app(cfg, Clock(NOW))) as client:
        unsaved = [{"name": "groq", "enabled": True, "base_url": api, "model": "llama-3.3-70b-versatile",
                    "api_key_env": "TEST_GOOD_KEY"}]
        r = client.post("/api/ai/models", json={"name": "groq", "providers": unsaved}).json()
        assert "openai/gpt-oss-120b" in r["models"]
        (res,) = client.post("/api/ai/test", json={"providers": unsaved}).json()["results"]
        assert res["ok"] is False and res["kind"] == "model"
        unsaved[0]["model"] = "openai/gpt-oss-120b"
        (res,) = client.post("/api/ai/test", json={"providers": unsaved}).json()["results"]
        assert res["ok"] is True
