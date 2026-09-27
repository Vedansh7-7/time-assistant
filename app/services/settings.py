"""User settings stored in SQLite, with defaults. Values are plain JSON."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..core.model import SearchSettings
from ..db import repo


def detect_timezone() -> str:
    if os.environ.get("TA_TZ"):
        return os.environ["TA_TZ"]
    try:
        tz = Path("/etc/timezone").read_text().strip()
        if tz:
            return tz
    except OSError:
        pass
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


DEFAULTS: dict = {
    "timezone": None,  # resolved lazily via detect_timezone()
    "search": {"day_start": "08:00", "day_end": "22:00", "step_min": 15, "horizon_days": 14, "max_results": 5},
    "reminders": {"default_offsets_min": [15], "grace_hours": 6, "ntfy_url": ""},
    "ai": {
        "enabled": False,
        "privacy_mode": "minimal",  # minimal | full
        "timeout_s": 45,
        # Tried in order. api_key_env names an environment variable (keys are never stored in the DB).
        "providers": [
            {"name": "groq", "enabled": True, "base_url": "https://api.groq.com/openai/v1",
             "model": "openai/gpt-oss-120b", "api_key_env": "GROQ_API_KEY"},
            {"name": "ollama", "enabled": False, "base_url": "http://localhost:11434/v1",
             "model": "qwen2.5:7b", "api_key_env": ""},
        ],
    },
    "backup": {"enabled": True, "interval_hours": 24, "keep": 14, "target_dir": ""},
    "public": {
        "enabled": False,
        "owner_name": "",
        "headline": "Let's find a time",
        "intro": "Tell me what it's about and pick a time.",
        "durations_min": [15, 30, 45, 60, 90],
        "days_ahead": 21,
        "min_notice_hours": 12,
        "day_start": "10:00", "day_end": "19:00",
        "weekdays": [0, 1, 2, 3, 4],
        "step_min": 30,
        "busy_label": "Reserved",
        "max_open_per_email": 3,
        "max_requests_per_ip_per_day": 5,
        "public_base_url": "",   # e.g. https://pi.tailnet-name.ts.net (used in email links)
    },
    "email": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com", "smtp_port": 587,
        "username": "", "password_env": "TA_SMTP_PASSWORD",
        "from_name": "", "reply_to": "",
    },
}

KEYS = tuple(DEFAULTS)


def _merge(default, value):
    if isinstance(default, dict) and isinstance(value, dict):
        out = copy.deepcopy(default)
        for k, v in value.items():
            out[k] = _merge(default.get(k), v) if k in default else v
        return out
    return copy.deepcopy(default) if value is None else value


def get(conn, key: str):
    val = _merge(DEFAULTS[key], repo.get_setting(conn, key))
    if key == "timezone" and not val:
        val = detect_timezone()
    return val


def get_all(conn) -> dict:
    return {k: get(conn, k) for k in KEYS}


def validate(key: str, value) -> None:
    if key not in DEFAULTS:
        raise ValueError(f"Unknown setting '{key}'")
    if key == "timezone":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ValueError(f"Unknown timezone '{value}'")
    if key == "search":
        s = _merge(DEFAULTS["search"], value)
        if _minutes(s["day_end"]) <= _minutes(s["day_start"]):
            raise ValueError("Search day end must be after day start")
        if not 5 <= int(s["step_min"]) <= 120:
            raise ValueError("Search step must be 5–120 minutes")
    if key == "ai":
        s = _merge(DEFAULTS["ai"], value)
        if s["privacy_mode"] not in ("minimal", "full"):
            raise ValueError("privacy_mode must be 'minimal' or 'full'")
    if key == "public":
        s = _merge(DEFAULTS["public"], value)
        if _minutes(s["day_end"]) <= _minutes(s["day_start"]):
            raise ValueError("Public day end must be after day start")
        if not s["durations_min"] or any(not 5 <= int(d) <= 480 for d in s["durations_min"]):
            raise ValueError("Public durations must be 5–480 minutes")
        if any(not 0 <= int(d) <= 6 for d in s["weekdays"]):
            raise ValueError("Weekdays must be 0 (Mon) – 6 (Sun)")


def put(conn, key: str, value) -> None:
    validate(key, value)
    repo.set_setting(conn, key, value)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def tz(conn) -> ZoneInfo:
    try:
        return ZoneInfo(get(conn, "timezone"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def search_settings(conn) -> SearchSettings:
    s = get(conn, "search")
    return SearchSettings(
        day_start_minute=_minutes(s["day_start"]), day_end_minute=_minutes(s["day_end"]),
        step_min=int(s["step_min"]), horizon_days=int(s["horizon_days"]), max_results=int(s["max_results"]),
    )
