# Personal Time & Commitment Assistant

A self-hosted scheduling assistant for one person, running on a Raspberry Pi 3B (1 GB RAM).

```
AI decides what you mean.  Core decides what is valid.  You decide what happens.
```

- **Deterministic core** (`app/core/`): pure Python with no DB, network or clock access. It holds the authority and conflict rules, recurrence, buffers, availability rules, slot search, tasks, and the confirmation policy. Every rule is covered by tests.
- **One canonical SQLite database** (`data/time_assistant.db`, WAL mode, versioned migrations).
- **Guardrail layer** (`app/services/core.py`): the single path every client uses. Each change is *planned* first. It either runs straight away (safe), is *rejected* with alternatives, or becomes a pending action with a token. That token runs only after a clear confirmation, and only after re-validating that nothing changed in the meantime.
- **Interfaces:** the web UI, the REST API (`/docs`), typed AI tools (`/api/tools`), MCP (`POST /mcp`), and the public booking page (`/book`, served on a separate port).
- **AI is optional.** Without internet or an API key, everything except free-text chat keeps working.

## Rules that are locked in

| Situation | Result |
|---|---|
| New level < existing level | Rejected, with ranked alternatives. You can raise the request's level or use FORCE. |
| New level > existing level | Asks first. The displaced item becomes **needs reschedule** (it isn't deleted). |
| Equal level, same status | You decide: keep existing / keep new / move new / change level. It never picks a winner for you. |
| Equal level, confirmed vs tentative | A confirmed request may displace a tentative one (after asking). A tentative request never displaces a confirmed one. |
| FORCE | Displaces everything overlapping. Always asks first. |
| Levels | 1–5, default 3. They live on the commitment only. Public bookings are always level 1. |
| Buffers / travel | Buffers only when you specify them. Travel time isn't modelled yet. |
| AI-initiated create, level/status change, profile edit | Always needs your confirmation. |
| Move/cancel a tentative item | Direct. Moving or cancelling a *confirmed* item asks first. |
| Bulk cancel/delete | You must type the exact phrase shown, e.g. `cancel 3 commitments`. |
| Chat confirmations | Only a clear yes counts ("yes", "go ahead"). "Hmm, okay..." is not a yes. A "yes" can only confirm something shown in the previous reply. |

## Machine lane (for your AI and scripts)

Use stable compact IDs (`C12` commitment, `C12@20261005T0430Z` occurrence, `P3` person, `T4` task, `R2` rule, `Q7` public request) and structured JSON. There's no prose to parse.

- `GET /api/tools` lists the typed tool catalogue with JSON schemas and a READ / PROPOSE / WRITE / DELETE / CONFIRM category for each.
- `POST /mcp` is an MCP server (Streamable HTTP) exposing the same tools. External agents can read and propose. Confirmations happen only in the web app.
- `POST /api/check/{kind}` is a dry run. `POST /api/actions/{kind}` submits a guarded action. `POST /api/pending/{token}/confirm` confirms it.
  The kinds are `create_commitment`, `update_commitment`, `move_occurrence`, `skip_occurrence`, `cancel_commitments`, `force_commitment`, `create_person`, `update_person` and `delete_tasks`.
- `GET /api/day/2026-09-29` returns the same object in two forms: `blocks` for machines and `text` for humans.

## Run locally (development)

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt      # Windows: .venv\Scripts\pip
TA_TZ=Asia/Kolkata .venv/bin/python -m app.serve    # owner :8000, public :8001
.venv/bin/python -m pytest                          # tests use throwaway databases only
```

## Deploy on the Pi

```bash
# copy this folder to the Pi, e.g. /home/pi/time-assistant, then:
bash deploy/install.sh              # venv, deps, systemd service, pre-upgrade DB backup
sudo nano /etc/time-assistant.env   # keys and timezone (see below)
sudo systemctl restart time-assistant
journalctl -u time-assistant -f     # logs
```

Both servers run in one Python process, and systemd caps it at 300 MB. Check the real usage on the Pi with `systemctl status time-assistant` (the Memory: line).

### Free AI (the Pi can't run a local model)
1. Create a free key at https://console.groq.com.
2. Put `GROQ_API_KEY=...` in `/etc/time-assistant.env` and restart the service.
3. In the web app, go to Preferences → AI → enable.

Any OpenAI-compatible provider works as a fallback (OpenRouter free models, Gemini's OpenAI endpoint, or Ollama on your laptop). Add them to the provider list in priority order. **Privacy mode** `minimal` sends only what a request needs. `full` also sends the next 7 days and your people list.

### Booking emails (free)
Gmail SMTP with an app password (about 500 mails/day):
1. Turn on 2-Step Verification for the Google account.
2. Create an app password at https://myaccount.google.com/apppasswords.
3. Set `TA_SMTP_PASSWORD=...` in `/etc/time-assistant.env`.
4. In Preferences → Email, enable it and set username = that Gmail address.

Mail goes through a persistent outbox, so if the Pi is offline it's sent later. Templates are in `app/templates/email/` (plain HTML with `{{placeholders}}`).

### Public booking page (free, no domain) with Tailscale Funnel
Only the public server (`127.0.0.1:8001`) is exposed. Your owner UI and API are never reachable from the internet.
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale funnel --bg 8001                 # -> https://<pi-name>.<tailnet>.ts.net  (public)
sudo tailscale serve --bg --https=8443 8000     # -> https://<pi-name>.<tailnet>.ts.net:8443 (only your devices)
```
Then, in Preferences → Public booking: enable it, set your name, and set `public_base_url` to the Funnel URL (used in email links). Install Tailscale on your phone and laptop to reach your own UI from anywhere through the private `:8443` address.

### Backups
A consistent online snapshot is taken every 24 h (configurable) into `data/backups/`, keeping the last 14. To copy to your backup device, mount it on the Pi (e.g. an SMB share, with the credentials in `/etc/fstab` or a credentials file, never in this app). Then set Preferences → Backup → `target_dir` to the mount path. Snapshots are integrity-checked. You can also use "Run backup now" on the System page.

## Not built yet (and why)
- **Passkey login:** passkeys require HTTPS. They'll be added on the Tailscale `:8443` address. Until then the owner UI trusts your LAN or tailnet, so don't port-forward 8000.
- **Travel time, and audit history/undo:** the schema keeps `created_at`/`updated_at` so history can be added later.
- **Web push:** browsers block notifications on plain-http LAN pages. Reminders show in the app and can be pushed through ntfy (Preferences → Reminders → `ntfy_url`).
- The old scaffold's `data/assistant.db` isn't migrated. The new database is `data/time_assistant.db`.
