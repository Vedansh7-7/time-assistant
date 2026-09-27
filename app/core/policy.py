"""Guardrail policy: which operations need which kind of user confirmation.

Pure and channel-aware. `channel` is "ui" for an explicit action in the web
app (the click itself is intent) or "ai" when the assistant initiated it.

Confirmation kinds:
  none    execute immediately after validation
  confirm clear yes (button, or unambiguous natural-language reply)
  typed   the user must type an exact phrase (destructive/bulk operations)
"""
from __future__ import annotations

from .model import Status

NONE, CONFIRM, TYPED = "none", "confirm", "typed"


def for_create(channel: str, displaces: int, rule_violations: int) -> str:
    if displaces or rule_violations:
        return CONFIRM
    # A commitment is never made official just because the AI proposed it.
    return CONFIRM if channel == "ai" else NONE


def for_update(channel: str, original: Status, time_changed: bool, level_changed: bool,
               status_changed: bool, displaces: int, rule_violations: int) -> str:
    if displaces or rule_violations:
        return CONFIRM
    if channel == "ai" and (level_changed or status_changed):
        return CONFIRM  # no silent authority or status changes
    if original == Status.CONFIRMED and (time_changed or level_changed or status_changed):
        return CONFIRM
    return NONE


def for_cancel(channel: str, original: Status) -> str:
    return NONE if original == Status.TENTATIVE else CONFIRM


def for_force() -> str:
    return CONFIRM


def for_bulk(count: int) -> str:
    return TYPED if count > 1 else CONFIRM


def for_profile_update(channel: str) -> str:
    return CONFIRM if channel == "ai" else NONE


def for_task_delete(count: int) -> str:
    return TYPED if count > 1 else CONFIRM


def typed_phrase(verb: str, count: int, noun: str) -> str:
    return f"{verb} {count} {noun}"
