"""Minimal MCP server (Streamable HTTP transport, JSON responses) at POST /mcp.

Exposes the same tool registry the built-in agent uses, with channel="ai".
External agents can read and propose, but can never confirm: consequential
actions stay pending until the user confirms them in the web app.
"""
from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import __version__
from .services import settings as settings_mod
from .services.core import CoreService
from .tools.registry import TOOLS, ToolContext, call_tool

PROTOCOL_VERSION = "2025-06-18"
HIDDEN = {"confirm_pending"}


def _tools() -> list[dict]:
    return [{"name": t.name, "description": f"[{t.category}] {t.description}", "inputSchema": t.parameters}
            for t in TOOLS if t.name not in HIDDEN]


def _result(id_, result) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _dispatch(msg: dict, open_conn, now_fn) -> dict | None:
    method, id_, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if id_ is None:  # notification
        return None
    if method == "initialize":
        return _result(id_, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "time-assistant", "version": __version__},
            "instructions": "Scheduling tools. WRITE tools return needs_confirmation; the user confirms in the web app.",
        })
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": _tools()})
    if method == "tools/call":
        name = params.get("name")
        if name in HIDDEN:
            return _error(id_, -32602, f"Tool {name} is not available over MCP")
        conn = open_conn()
        try:
            svc = CoreService(conn, now_fn)
            privacy = settings_mod.get(conn, "ai").get("privacy_mode", "minimal")
            ctx = ToolContext(svc, privacy, reply_confirm_allowed=False)
            out = call_tool(ctx, name, params.get("arguments") or {})
        finally:
            conn.close()
        return _result(id_, {"content": [{"type": "text", "text": json.dumps(out, default=str)}],
                             "structuredContent": out, "isError": "error" in out})
    return _error(id_, -32601, f"Method not found: {method}")


async def handle_mcp(request: Request, open_conn, now_fn):
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    if isinstance(body, list):
        out = [r for r in (_dispatch(m, open_conn, now_fn) for m in body) if r is not None]
        return JSONResponse(out) if out else Response(status_code=202)
    r = _dispatch(body, open_conn, now_fn)
    return JSONResponse(r) if r is not None else Response(status_code=202)
