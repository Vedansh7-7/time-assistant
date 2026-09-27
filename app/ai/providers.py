"""Provider-independent AI access.

Every supported backend (Groq, OpenRouter, Ollama, llama.cpp server, OpenAI,
...) speaks the OpenAI-compatible /chat/completions protocol with tools, so a
single adapter covers them; the user configures an ordered provider list and
the first one that answers wins. Stdlib only (urllib) to stay light on a Pi.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


class ProviderError(Exception):
    pass


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

    def _post(self, body: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url.rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            raise ProviderError(f"{self.name}: HTTP {e.code} {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ProviderError(f"{self.name}: unreachable ({e})") from e

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": 0.2}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        data = await asyncio.to_thread(self._post, body)
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"{self.name}: malformed response")


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
        raise ProviderError("; ".join(self.errors) or "no AI provider configured")


async def status(ai_cfg: dict) -> list[dict]:
    return [{"name": p.name, "model": p.model, "base_url": p.base_url, "enabled": p.enabled,
             "configured": p.configured,
             "missing_key": bool(p.api_key_env) and not p.api_key} for p in from_settings(ai_cfg)]
