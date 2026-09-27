"""Email via SMTP (free with a Gmail app password) through a persistent outbox.

Mails are queued in SQLite first and sent by a background loop, so an offline
Pi never loses a notification: it is sent when connectivity returns.
Templates live in app/templates/email/ and are plain HTML with {{placeholders}};
values are HTML-escaped before substitution.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from ..db.connection import utcnow, write_tx
from . import settings as settings_mod

log = logging.getLogger("mailer")
TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "email"
MAX_ATTEMPTS = 8
_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(template: str, **values) -> tuple[str, str]:
    """(html, plain_text). `body_html` values marked safe by the caller are not escaped."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    body = (TEMPLATES / f"{template}.html").read_text(encoding="utf-8")
    esc = {k: (v if k.endswith("_html") else html.escape(str(v or ""))) for k, v in values.items()}
    body = _VAR.sub(lambda m: esc.get(m.group(1), ""), body)
    page = _VAR.sub(lambda m: body if m.group(1) == "content" else esc.get(m.group(1), ""), base)
    text = re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>|</p>|</h\d>|</li>", "\n", body))
    text = re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()
    return page, text


def queue(conn, to_addr: str, subject: str, template: str, **values) -> None:
    page, text = render(template, subject=subject, **values)
    conn.execute("INSERT INTO outbox (to_addr, subject, html, text, created_at) VALUES (?,?,?,?,?)",
                 (to_addr, subject, page, text, utcnow().isoformat()))


def _send(cfg: dict, to_addr: str, subject: str, page: str, text: str) -> None:
    password = os.environ.get(cfg.get("password_env") or "", "")
    if not (cfg["username"] and password):
        raise RuntimeError("SMTP username or password (env) missing")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.get("from_name") or "", cfg["username"]))
    msg["To"] = to_addr
    if cfg.get("reply_to"):
        msg["Reply-To"] = cfg["reply_to"]
    msg.set_content(text)
    msg.add_alternative(page, subtype="html")
    port = int(cfg["smtp_port"])
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(cfg["smtp_host"], port, context=ctx, timeout=20) as s:
            s.login(cfg["username"], password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["smtp_host"], port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(cfg["username"], password)
            s.send_message(msg)


def flush(conn) -> int:
    """Send queued mail. Returns the number sent."""
    cfg = settings_mod.get(conn, "email")
    if not cfg["enabled"]:
        return 0
    rows = conn.execute("SELECT * FROM outbox WHERE status = 'queued' ORDER BY id LIMIT 20").fetchall()
    sent = 0
    for r in rows:
        try:
            _send(cfg, r["to_addr"], r["subject"], r["html"], r["text"])
            with write_tx(conn):
                conn.execute("UPDATE outbox SET status='sent', sent_at=?, attempts=attempts+1 WHERE id=?",
                             (utcnow().isoformat(), r["id"]))
            sent += 1
        except Exception as e:  # network down, auth error, ...: retry later
            status = "failed" if r["attempts"] + 1 >= MAX_ATTEMPTS else "queued"
            with write_tx(conn):
                conn.execute("UPDATE outbox SET status=?, attempts=attempts+1, last_error=? WHERE id=?",
                             (status, str(e)[:300], r["id"]))
            log.warning("mail to %s failed: %s", r["to_addr"], e)
            break  # likely offline; stop this round
    return sent


async def run_loop(open_conn, interval_s: int = 60) -> None:
    while True:
        try:
            conn = open_conn()
            try:
                await asyncio.to_thread(flush, conn)
            finally:
                conn.close()
        except Exception:
            log.exception("mail loop error")
        await asyncio.sleep(interval_s)
