#!/bin/bash
# Nightly self-update from GitHub (run by time-assistant-update.timer).
#
#   1. fetch origin/main; stop if nothing new
#   2. back up the database
#   3. switch to the new commit, install dependencies if they changed
#   4. run the tests; if they fail, go back
#   5. restart and check health; if unhealthy, go back (and restore the DB if its schema changed)
#
# Run by hand:  sudo systemctl start time-assistant-update
# See the log:  journalctl -u time-assistant-update -n 50
set -uo pipefail

# git reset below rewrites this file, and bash reads scripts as it runs them,
# so run from a private copy.
if [ -z "${TA_UPDATE_COPY:-}" ]; then
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  COPY="$(mktemp /tmp/time-assistant-update.XXXXXX)"
  cp "$0" "$COPY"
  TA_UPDATE_COPY=1 exec bash "$COPY" "$ROOT"
fi
trap 'rm -f "$0"' EXIT
cd "$1"

DB=data/time_assistant.db
PY=.venv/bin/python
log() { echo "[update] $*"; }

schema_version() { [ -f "$DB" ] && sqlite3 "$DB" "PRAGMA user_version" 2>/dev/null || echo 0; }

install_deps() {
  if ! cmp -s requirements-dev.txt .venv/.deps 2>/dev/null || ! cmp -s requirements.txt .venv/.deps-main 2>/dev/null; then
    log "installing dependencies"
    .venv/bin/pip install -q -r requirements-dev.txt || return 1
    cp requirements-dev.txt .venv/.deps && cp requirements.txt .venv/.deps-main
  fi
}

healthy() {
  for _ in $(seq 1 15); do
    curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

git fetch --quiet origin main || { log "could not reach GitHub (offline?); will try tomorrow"; exit 0; }
OLD=$(git rev-parse HEAD)
NEW=$(git rev-parse origin/main)
if [ "$OLD" = "$NEW" ]; then log "already up to date ($(git rev-parse --short HEAD))"; exit 0; fi
log "updating $(git rev-parse --short "$OLD") -> $(git rev-parse --short "$NEW")"
git log --oneline "$OLD..$NEW" | sed 's/^/[update]   /'

# 2. Backup (keep the last 10 pre-update copies)
mkdir -p data/backups
BACKUP=""
if [ -f "$DB" ]; then
  BACKUP="data/backups/pre-update-$(date +%Y%m%d-%H%M%S).db"
  sqlite3 "$DB" ".backup '$BACKUP'" || { log "backup failed; not updating"; exit 1; }
  ls -1t data/backups/pre-update-*.db 2>/dev/null | tail -n +11 | xargs -r rm -f
fi
SCHEMA_BEFORE=$(schema_version)

rollback() {
  log "ROLLING BACK to $(git rev-parse --short "$OLD"): $1"
  git reset --quiet --hard "$OLD"
  install_deps
  if [ -n "$BACKUP" ] && [ "$(schema_version)" != "$SCHEMA_BEFORE" ]; then
    log "database schema changed during the failed update; restoring $BACKUP"
    sudo systemctl stop time-assistant
    cp "$BACKUP" "$DB" && rm -f "$DB-wal" "$DB-shm"
  fi
  sudo systemctl restart time-assistant
  healthy && log "rolled back; running $(git rev-parse --short HEAD)" || log "WARNING: still unhealthy after rollback"
  exit 1
}

# 3. Switch
git reset --quiet --hard "$NEW"
install_deps || rollback "dependency install failed"

# 4. Tests (throwaway databases only; never touches data/)
log "running tests"
if ! TA_BACKGROUND=0 "$PY" -m pytest -q -x --no-header -p no:cacheprovider >/tmp/time-assistant-tests.log 2>&1; then
  tail -n 20 /tmp/time-assistant-tests.log | sed 's/^/[update]   /'
  rollback "tests failed"
fi
log "tests passed"

# 5. Restart + health
sudo systemctl restart time-assistant
healthy || rollback "app did not become healthy after restart"
log "done; running $(git rev-parse --short HEAD)"
