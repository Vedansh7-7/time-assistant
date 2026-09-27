# Pi setup: commands to type

Two places:
- **PC**: PowerShell on your laptop.
- **Pi**: after `ssh`-ing into the Pi.

Replace `pi` with your Pi username and `10.202.6.57` with its IP (run `hostname -I` on the Pi to see it).

## First install

**1. PC**: pack the code and copy it to the Pi.
```powershell
cd E:\claude\Deals\personal-assitant
git archive --format=tar -o $env:TEMP\ta.tar HEAD
scp $env:TEMP\ta.tar pi@10.202.6.57:/tmp/ta.tar
```

**2. PC**: log in to the Pi.
```powershell
ssh pi@10.202.6.57
```

**3. Pi**: stop the old app and unpack the new one.
```bash
pkill -f "uvicorn app.main:app"
mkdir -p ~/time-assistant && cd ~/time-assistant
tar -xf /tmp/ta.tar && rm /tmp/ta.tar
```

**4. Pi**: install Python packages (5 to 10 minutes on a Pi 3).
```bash
sudo apt update && sudo apt install -y python3-venv sqlite3 curl
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**5. Pi**: test run. Open `http://10.202.6.57:8000` on your phone, then press Ctrl+C.
```bash
TA_TZ=Asia/Kolkata .venv/bin/python -m app.serve
```

**6. Pi**: settings file. Put your Groq key after `GROQ_API_KEY=`, then save (Ctrl+O, Enter, Ctrl+X).
```bash
sudo cp deploy/time-assistant.env.example /etc/time-assistant.env
sudo chmod 600 /etc/time-assistant.env
sudo nano /etc/time-assistant.env
```
A free key comes from https://console.groq.com (API Keys). After step 7, turn the assistant on in the app under Settings > Assistant.

**7. Pi**: run it as a service, so it starts on boot.
```bash
sed -e "s#__USER__#$USER#" -e "s#__DIR__#$HOME/time-assistant#" deploy/time-assistant.service | sudo tee /etc/systemd/system/time-assistant.service
sudo systemctl daemon-reload
sudo systemctl enable --now time-assistant
curl http://127.0.0.1:8000/health
```
The last line should print `{"ok":true,...}`.

**8. Router**: reserve the Pi's IP (often called "DHCP reservation") so the address doesn't change after a reboot.

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

## Updating later

**PC**: repeat step 1.

**Pi**:
```bash
cd ~/time-assistant
sqlite3 data/time_assistant.db ".backup data/before-update.db"
rm -rf app tests deploy
tar -xf /tmp/ta.tar && rm /tmp/ta.tar
.venv/bin/pip install -r requirements.txt
sudo systemctl restart time-assistant
```
Your data in `data/` is never overwritten.

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
| `pip install` fails | `sudo apt install -y python3-dev build-essential` then retry step 4 |
| Service won't start after an update | `cat /etc/systemd/system/time-assistant.service` and check the paths |

Until login is added, keep port 8000 on your home Wi-Fi only (don't port-forward it).
