"""Deterministic classification of natural-language confirmation replies.

This is a second lock under the AI: even if the model decides the user said
yes, a pending action only executes when the user's actual message is an
unambiguous affirmative. Hedges ("hmm, okay...", "I guess") are ambiguous.
"""
from __future__ import annotations

import re

AFFIRM, DENY, AMBIGUOUS = "affirm", "deny", "ambiguous"

_AFFIRMATIVE = {
    "yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed",
    "do it", "go ahead", "go for it", "commit", "commit it", "book it", "schedule it",
    "that's fine", "thats fine", "that is fine", "fine", "sounds good", "perfect",
    "yes please", "please do", "absolutely", "definitely", "correct", "approved", "approve",
    "yes do it", "yes go ahead", "yes commit it", "ok do it", "okay do it", "sure go ahead",
    "yes confirm", "move it", "force it", "replace it", "keep new", "proceed",
}
_NEGATIVE = {
    "no", "n", "nope", "nah", "don't", "dont", "do not", "stop", "cancel that", "never mind",
    "nevermind", "not now", "wait", "hold on", "no thanks", "no thank you", "abort", "skip",
}
_HEDGES = re.compile(
    r"\b(hmm+|umm*|uh+|maybe|perhaps|i guess|i think|not sure|probably|possibly|kind of|kinda|"
    r"sort of|dunno|idk|let me think|should i|what if|but)\b|\?|\.\.\.|…"
)


def _normalize(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[!.,]+$", "", t)  # trailing punctuation (but '...' is caught as a hedge first)
    t = re.sub(r"\s+", " ", t)
    return t


def classify_reply(text: str) -> str:
    raw = (text or "").strip().lower()
    if not raw:
        return AMBIGUOUS
    if _HEDGES.search(raw):
        return AMBIGUOUS
    t = _normalize(raw)
    if t in _NEGATIVE:
        return DENY
    if t in _AFFIRMATIVE:
        return AFFIRM
    # Allow short affirmative + politeness, e.g. "yes thanks", "sure, go ahead".
    words = re.sub(r"[^\w' ]", " ", t).split()
    if words and len(words) <= 5:
        tail_ok = {"please", "thanks", "thank", "you", "it", "now", "go", "ahead", "do", "that"}
        if words[0] in {"yes", "yeah", "yep", "sure", "ok", "okay", "confirm"} and all(
                w in tail_ok for w in words[1:]):
            return AFFIRM
        if words[0] in {"no", "nope", "nah"}:
            return DENY
    return AMBIGUOUS


def matches_typed_phrase(text: str, phrase: str) -> bool:
    return _normalize(text or "") == _normalize(phrase)
