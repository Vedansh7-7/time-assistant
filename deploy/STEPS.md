# Pi setup: commands to type

Two places:
- **PC**: PowerShell on your laptop.
- **Pi**: after `ssh`-ing into the Pi.

Replace `pi` with your Pi username and `10.202.6.57` with its IP (run `hostname -I` on the Pi to see it).

## First install

**1. PC**: log in to the Pi.
```powershell
ssh pi@10.202.6.57
```

**2. Pi**: stop the old app and get the code from GitHub.
```bash
pkill -f "uvicorn app.main:app"
sudo apt update && sudo apt install -y git python3-venv sqlite3 curl
git clone https://github.com/Vedansh7-7/time-assistant.git ~/time-assistant
cd ~/time-assistant
```
Already installed an earlier copy in `~/time-assistant` with `scp`? Turn it into a clone instead (your `data/` stays):
```bash
cd ~/time-assistant
git init -q && git remote add origin https://github.com/Vedansh7-7/time-assistant.git
git fetch -q origin main && git reset --hard origin/main && git branch -u origin/main
```

**3. Pi**: install Python packages (5 to 10 minutes on a Pi 3). The dev file adds the test tools the nightly update uses.
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

**4. Pi**: test run. Open `http://10.202.6.57:8000` on your phone, then press Ctrl+C.
```bash
TA_TZ=Asia/Kolkata .venv/bin/python -m app.serve
```

**5. Pi**: settings file. Put your Groq key after `GROQ_API_KEY=`, then save (Ctrl+O, Enter, Ctrl+X).
```bash
sudo cp deploy/time-assistant.env.example /etc/time-assistant.env
sudo chmod 600 /etc/time-assistant.env
sudo nano /etc/time-assistant.env
```
A free key comes from https://console.groq.com (API Keys). After step 6, turn the assistant on in the app under Settings > Assistant.

**6. Pi**: run it as a service, so it starts on boot.
```bash
sed -e "s#__USER__#$USER#" -e "s#__DIR__#$HOME/time-assistant#" deploy/time-assistant.service | sudo tee /etc/systemd/system/time-assistant.service
sudo systemctl daemon-reload
sudo systemctl enable --now time-assistant
curl http://127.0.0.1:8000/health
```
The last line should print `{"ok":true,...}`.

**7. Router**: reserve the Pi's IP (often called "DHCP reservation") so the address doesn't change after a reboot.

## Everyday (Pi)

```bash
systemctl status time-assistant                      # running? memory
journalctl -u time-assistant -f                      # live log, Ctrl+C to stop
sudo systemctl restart time-assistant                # restart
sudo nano /etc/time-assistant.env                    # edit keys, then restart
curl -X POST http://127.0.0.1:8000/api/backup/run    # back up now
free -h                                              # memory
df -h /                                              # disk space
```

**PC**: copy the Pi's backups to your laptop.
```powershell
mkdir E:\claude\Deals\backups -Force
scp "pi@10.202.6.57:~/time-assistant/data/backups/*.db" E:\claude\Deals\backups\
```

## Nightly updates from GitHub

Every night at 23:59 the Pi checks `main` on GitHub. If there are new commits it backs up the database,
runs the tests, restarts and checks health. If the tests fail or the app won't start, it goes back to
the previous version on its own.

**Turn it on (Pi, one time):**
```bash
cd ~/time-assistant
sudo timedatectl set-timezone Asia/Kolkata
for f in time-assistant-update.service time-assistant-update.timer; do
  sed -e "s#__USER__#$USER#" -e "s#__DIR__#$HOME/time-assistant#" deploy/$f | sudo tee /etc/systemd/system/$f >/dev/null
done
sed "s#__USER__#$USER#" deploy/sudoers-time-assistant | sudo tee /etc/sudoers.d/time-assistant >/dev/null
sudo chmod 440 /etc/sudoers.d/time-assistant && sudo visudo -c
sudo systemctl daemon-reload
sudo systemctl enable --now time-assistant-update.timer
systemctl list-timers time-assistant-update
```
The last line shows the next run (tonight 23:59). `visudo -c` must say "parsed OK".

**Check it (Pi):**
```bash
sudo systemctl start time-assistant-update       # run an update check right now
journalctl -u time-assistant-update -n 40        # what the last runs did
```

**Ship a change (PC):** commit and push; the Pi picks it up at 23:59.
```powershell
git push
```

## Optional: log in without a password

**PC**:
```powershell
ssh-keygen -t ed25519
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh pi@10.202.6.57 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```
Press Enter at the ssh-keygen prompts. After this, `ssh` and `scp` no longer ask for the Pi password.

## If something goes wrong

| Symptom | Try (Pi) |
|---|---|
| Page won't load | `journalctl -u time-assistant -n 50` and send me the output |
| "Address already in use" in the log | `pkill -f "uvicorn app.main:app"` then `sudo systemctl restart time-assistant` |
| `pip install` fails | `sudo apt install -y python3-dev build-essential` then retry step 3 |
| Service won't start after an update | `cat /etc/systemd/system/time-assistant.service` and check the paths |
| Nightly update keeps rolling back | `journalctl -u time-assistant-update -n 60` shows the failing test; fix it on the PC and push |
| Update log says "sudo: a password is required" | Redo the sudoers lines in "Nightly updates" |

Until login is added, keep port 8000 on your home Wi-Fi only (don't port-forward it).
