"""Provider-independent AI access.

Every supported backend (Groq, OpenRouter, Ollama, llama.cpp server, OpenAI,
...) speaks the OpenAI-compatible /chat/completions protocol with tools, so a
single adapter covers them; the user configures an ordered provider list and
the first one that answers wins. Stdlib only (urllib) to stay light on a Pi.
"""
from __future__ import annotations

import asyncio
import time
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass


# Cloudflare (in front of Groq and others) rejects Python's default "Python-urllib" agent
# with 403 "error code: 1010", so identify the app explicitly.
USER_AGENT = "time-assistant/0.2 (+https://github.com/Vedansh7-7/time-assistant)"


class ProviderError(Exception):
    """kind: model | auth | rate | blocked | unreachable | bad_response | config"""

    def __init__(self, message: str, kind: str = "bad_response", retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


def _retry_after(headers, body: str) -> float | None:
    """Seconds to wait, from the Retry-After header or Groq's "Please try again in 7.66s"."""
    try:
        if headers and headers.get("retry-after"):
            return float(headers.get("retry-after"))
    except ValueError:
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", body)
    return (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else None


def _kind(status: int, body: str) -> str:
    if "error code: 10" in body:  # Cloudflare block (1010 bad agent, 1020 firewall rule, ...)
        return "blocked"
    try:
        err = json.loads(body).get("error", {})
        code = str(err.get("code") or err.get("type") or "")
    except (ValueError, AttributeError):
        code = ""
    if code in ("model_not_found", "model_decommissioned") or "model" in code and "not" in code:
        return "model"
    if status in (401, 403) or "api_key" in code:
        return "auth"
    if status == 429:
        return "rate"
    return "bad_response"


@dataclass
class Provider:
    name: str
    base_url: str
    model: str
    api_key_env: str = ""
    enabled: bool = True
    timeout_s: float = 45

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.base_url and self.model) and (not self.api_key_env or bool(self.api_key))

    def _request(self, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url.rstrip("/") + path, headers=headers,
                                     data=None if body is None else json.dumps(body).encode(),
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout_s) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:400].decode(errors="replace")
            raise ProviderError(f"{self.name}: HTTP {e.code} {detail}", _kind(e.code, detail),
                                _retry_after(e.headers, detail)) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ProviderError(f"{self.name}: unreachable ({e})", "unreachable") from e
        except ValueError as e:
            raise ProviderError(f"{self.name}: malformed response", "bad_response") from e

    def _post(self, body: dict) -> dict:
        return self._request("/chat/completions", body)

    async def list_models(self) -> list[str]:
        """Models this key can use, from the provider's OpenAI-compatible /models endpoint."""
        data = await asyncio.to_thread(self._request, "/models", None, 15)
        return sorted(m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id"))

    async def ping(self) -> dict:
        """One tiny request; returns {ok, ms, kind, error} for the settings Test button."""
        if not self.base_url or not self.model:
            return {"ok": False, "ms": 0, "kind": "config", "error": "Base URL and model are required."}
        if self.api_key_env and not self.api_key:
            return {"ok": False, "ms": 0, "kind": "config",
                    "error": f"The server has no {self.api_key_env} set. Add it on the Pi and restart."}
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(self._request, "/chat/completions",
                                    {"model": self.model, "messages": [{"role": "user", "content": "Reply with OK."}],
                                     "max_tokens": 5}, 20)
            return {"ok": True, "ms": int((time.monotonic() - t0) * 1000), "kind": None, "error": None}
        except ProviderError as e:
            return {"ok": False, "ms": int((time.monotonic() - t0) * 1000), "kind": e.kind, "error": str(e)[:300]}

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": 0.2}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        try:
            data = await asyncio.to_thread(self._post, body)
        except ProviderError as e:
            # A short rate-limit wait is cheaper than failing the whole question.
            if e.kind != "rate" or e.retry_after is None or e.retry_after > 20:
                raise
            await asyncio.sleep(e.retry_after + 0.5)
            data = await asyncio.to_thread(self._post, body)
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"{self.name}: malformed response", "bad_response")


def from_settings(ai_cfg: dict) -> list[Provider]:
    out = []
    for p in ai_cfg.get("providers") or []:
        out.append(Provider(p.get("name", "provider"), p.get("base_url", ""), p.get("model", ""),
                            p.get("api_key_env", ""), bool(p.get("enabled", True)), float(ai_cfg.get("timeout_s", 45))))
    return out


class ProviderChain:
    """Tries configured providers in priority order; remembers which one answered."""

    def __init__(self, providers: list[Provider]):
        self.providers = [p for p in providers if p.configured]
        self.used: str | None = None
        self.errors: list[str] = []
        self.kinds: list[str] = []

    @property
    def available(self) -> bool:
        return bool(self.providers)

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        for p in self.providers:
            try:
                msg = await p.chat(messages, tools)
                self.used = p.name
                return msg
            except ProviderError as e:
                self.errors.append(str(e))
                self.kinds.append(e.kind)
        # Report the most actionable failure: a wrong model or key beats "unreachable".
        for kind in ("model", "auth", "rate", "blocked", "bad_response", "unreachable"):
            if kind in self.kinds:
                raise ProviderError("; ".join(self.errors), kind)
        raise ProviderError("no AI provider configured", "config")


async def status(ai_cfg: dict) -> list[dict]:
    return [{"name": p.name, "model": p.model, "base_url": p.base_url, "enabled": p.enabled,
             "configured": p.configured,
             "missing_key": bool(p.api_key_env) and not p.api_key} for p in from_settings(ai_cfg)]
