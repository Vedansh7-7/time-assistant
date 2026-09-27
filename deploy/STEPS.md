# Pi setup, step by step

Run the commands in **PowerShell on your laptop**, from the project folder
(`E:\claude\Deals\personal-assitant`), unless a step says "on the Pi". Everything here is free.

## 1. One-time laptop setup

1. Check SSH works: `ssh -V`. If the command is missing, go to Windows Settings > System > Optional features > Add "OpenSSH Client".
2. Copy the Pi settings file:
   ```powershell
   Copy-Item deploy\pi.config.example deploy\pi.config
   notepad deploy\pi.config
   ```
   Set `PI_USER` to your Pi's username (the one you log in with) and `PI_DIR` to `/home/<that user>/time-assistant`.
   Set `PI_HOST` to the Pi's IP. To find it, run `hostname -I` on the Pi.
3. Install an SSH key so you never type the password again:
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\ssh-setup.ps1
   ```
   It asks for the Pi password once. Afterwards `ssh timepi` logs straight in.

## 2. Stop the old version (on the Pi, one time)

The old app from the zip may still be running on port 8000.
```powershell
ssh timepi
```
Then on the Pi:
```bash
pkill -f "uvicorn app.main:app" || true      # stops the old manual run
exit
```
Your old `data/assistant.db` is left alone. The new app uses a new file.

## 3. Deploy

```powershell
powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1
```
This runs the tests, uploads the last commit, installs Python packages (the first time takes 5 to 10 minutes on a Pi 3), sets up the `time-assistant` service so it starts on boot, and checks it's healthy.

Open `http://<PI_HOST>:8000` on your phone or laptop (same Wi-Fi).

Every later update is just: commit, then run `deploy\deploy.ps1` again. Your database and backups are never touched, and the database is backed up before each restart.

## 4. First settings (in the web app)

Go to **Settings** and fill in:
1. Schedule: your timezone (e.g. `Asia/Kolkata`) and day hours.
2. Availability: times you keep clear (e.g. lunch) and preferred meeting times.
3. Reminders: the default offset (15 min is set).

Then add your people and your recurring commitments (classes and so on).

## 5. Turn on the assistant (free Groq key)

1. Create a key at https://console.groq.com > API Keys.
2. Put it on the Pi:
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\pi.ps1 env
   ```
   Set `GROQ_API_KEY=...`, save (Ctrl+O, Enter, Ctrl+X), and the service restarts.
3. In the app go to Settings > Assistant and switch it on. Press (+) and choose Ask to try it.

## 6. Keep the Pi's address fixed

In your router's admin page, reserve the Pi's current IP (often called "DHCP reservation").
Otherwise the address can change after a reboot.

## 7. Everyday commands

```powershell
powershell -ExecutionPolicy Bypass -File deploy\pi.ps1 status       # running? memory, disk, version
powershell -ExecutionPolicy Bypass -File deploy\pi.ps1 logs         # live log (Ctrl+C to stop)
powershell -ExecutionPolicy Bypass -File deploy\pi.ps1 restart
powershell -ExecutionPolicy Bypass -File deploy\pi.ps1 backup       # back up now
powershell -ExecutionPolicy Bypass -File deploy\pi.ps1 pull-backup  # copy the newest backup to this laptop
```
Running `pull-backup` now and then keeps a copy of your data off the Pi.

## 8. Next (after it has run a few days)

These come in the next build, in this order:
1. **Tailscale + login.** Private HTTPS access from anywhere to your own app, and passkey sign-in.
   Until then, keep port 8000 on your home Wi-Fi only (don't port-forward it).
2. **Public booking page.** Tailscale Funnel (free) plus a Gmail app password for the emails.
   The README has the commands; we'll do it together once login is in place.

## If something goes wrong

| Symptom | Try |
|---|---|
| `ssh timepi` asks for a password | Re-run `deploy\ssh-setup.ps1` |
| Deploy says "Tests failed" | Run `.venv\Scripts\python -m pytest` and send me the output |
| Page won't load after deploy | `deploy\pi.ps1 logs` and send me the last lines |
| "Address already in use" in the logs | The old version is still running; redo step 2 |
| The Pi feels slow | `deploy\pi.ps1 status` shows memory; the service is capped at 300 MB |
