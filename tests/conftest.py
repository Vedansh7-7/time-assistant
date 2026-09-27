"""Test fixtures. Every test uses its own throwaway database — never the real one."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

os.environ["TA_BACKGROUND"] = "0"

from app.db import repo  # noqa: E402
from app.db.connection import connect, migrate  # noqa: E402
from app.services.core import CoreService  # noqa: E402

TZ = ZoneInfo("Asia/Kolkata")
# Monday 28 Sep 2026, 09:00 local
NOW = datetime(2026, 9, 28, 9, 0, tzinfo=TZ).astimezone(timezone.utc)


def local(day: int, hour: int, minute: int = 0, month: int = 9) -> str:
    """Naive local ISO string, as clients send it."""
    return datetime(2026, month, day, hour, minute).isoformat(timespec="minutes")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    repo.set_setting(c, "timezone", "Asia/Kolkata")
    yield c
    c.close()


@pytest.fixture
def svc(conn, clock):
    return CoreService(conn, clock)


def mk(svc, title, start, end, level=3, status="confirmed", **kw):
    """Create directly through the UI channel; asserts it executed."""
    r = svc.submit("create_commitment", {"title": title, "start": start, "end": end,
                                         "authority_level": level, "status": status, **kw}, "ui")
    assert r["status"] == "executed", r
    return r["result"]["commitment"]["id"]
