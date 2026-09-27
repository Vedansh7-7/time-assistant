#!/bin/bash
# Same as deploy.ps1, for Git Bash / Linux / macOS.
#   bash deploy/deploy.sh [--skip-tests]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f deploy/pi.config ] || { echo "Copy deploy/pi.config.example to deploy/pi.config first."; exit 1; }
# shellcheck disable=SC1091
source <(grep -E '^[A-Z_]+=' deploy/pi.config)
TARGET="$PI_USER@$PI_HOST"
grep -qE "^\s*Host\s+$PI_ALIAS\s*$" ~/.ssh/config 2>/dev/null && TARGET="$PI_ALIAS"

if [ -n "$(git status --porcelain)" ]; then echo "Uncommitted changes; commit first (only HEAD is deployed)."; exit 1; fi
if [ "${1:-}" != "--skip-tests" ]; then
  PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
  "$PY" -m pytest -q --no-header -p no:cacheprovider
fi

REV=$(git rev-parse --short HEAD)
git archive --format=tar HEAD | ssh "$TARGET" "cat > /tmp/time-assistant.tar"
ssh "$TARGET" bash -s <<EOF
set -e
mkdir -p '$PI_DIR' && cd '$PI_DIR'
rm -rf app tests deploy
tar -xf /tmp/time-assistant.tar -C '$PI_DIR' && rm -f /tmp/time-assistant.tar
echo '$REV' > REVISION
bash deploy/install.sh
for i in \$(seq 1 10); do curl -fsS http://127.0.0.1:8000/health && exit 0; sleep 2; done
journalctl -u time-assistant -n 30 --no-pager; exit 1
EOF
echo; echo "Deployed $REV. Open http://$PI_HOST:8000"
