"""Versioned static assets.

Pages reference their CSS/JS under /static/v/<build>/... (owner) and /book/v/<build>/... (public),
where <build> is a hash of the files' contents. Those URLs are cached by browsers forever
(`immutable`), so repeat visits make no requests for them at all, and a new release can never
run against a stale cached script: its URLs are different. The HTML pages themselves are
always revalidated.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
PUBLIC = STATIC / "public"
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
REVALIDATE = {"Cache-Control": "no-cache"}


@lru_cache(maxsize=1)
def build_id() -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in STATIC.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(STATIC)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


def resolve(base: Path, path: str) -> Path | None:
    """A file under `base`, or None (blocks ../ escapes)."""
    f = (base / path).resolve()
    return f if base.resolve() in f.parents and f.is_file() else None


@lru_cache(maxsize=1)
def owner_index() -> str:
    b = build_id()
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace('"/static/', f'"/static/v/{b}/')
    # Fetch every module in parallel instead of discovering imports one level at a time.
    preload = "".join(f'  <link rel="modulepreload" href="/static/v/{b}/js/{p.name}">\n'
                      for p in sorted((STATIC / "js").glob("*.js")))
    return html.replace("</head>", preload + "</head>", 1)


@lru_cache(maxsize=1)
def public_index() -> str:
    b = build_id()
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    html = html.replace('"/book/assets/', f'"/book/v/{b}/assets/').replace('"/book/fonts/', f'"/book/v/{b}/fonts/')
    return re.sub(r'"/book/theme\.css"', f'"/book/v/{b}/theme.css"', html)


def public_file(path: str) -> Path | None:
    """Map /book/v/<build>/<path> to a file: assets/* from public/, theme.css and fonts/* from static/."""
    if path.startswith("assets/"):
        return resolve(PUBLIC, path[len("assets/"):])
    if path == "theme.css" or path.startswith("fonts/"):
        return resolve(STATIC, path)
    return None
