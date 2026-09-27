#!/bin/bash
# Install/upgrade on Raspberry Pi OS Lite (Debian Trixie, Python 3.13). Run from the project folder:
#   bash deploy/install.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
cd "$DIR"

sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv sqlite3

if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# Back up the database before upgrading (schema migrations run automatically on start).
if [ -f data/time_assistant.db ]; then
  mkdir -p data/backups
  sqlite3 data/time_assistant.db ".backup 'data/backups/pre-upgrade-$(date +%Y%m%d%H%M%S).db'"
fi

if [ ! -f /etc/time-assistant.env ]; then
  sudo cp deploy/time-assistant.env.example /etc/time-assistant.env
  sudo chmod 600 /etc/time-assistant.env
  sudo chown root:root /etc/time-assistant.env
  echo "Created /etc/time-assistant.env - edit it to add keys (sudo nano /etc/time-assistant.env)."
fi

sed -e "s#__USER__#${USER_NAME}#g" -e "s#__DIR__#${DIR}#g" deploy/time-assistant.service \
  | sudo tee /etc/systemd/system/time-assistant.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now time-assistant
sudo systemctl restart time-assistant
sleep 3
systemctl --no-pager --lines=5 status time-assistant || true
echo
echo "Owner UI:  http://$(hostname -I | awk '{print $1}'):8000"
echo "Public page (local test): curl -s http://127.0.0.1:8001/api/public/profile"
