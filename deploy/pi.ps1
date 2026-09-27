# Everyday Pi commands from this laptop.
#   powershell -ExecutionPolicy Bypass -File deploy\pi.ps1 <command>
#
#   status        service status, memory, disk, deployed revision
#   logs          follow the service log (Ctrl+C to stop)
#   restart       restart the service
#   health        owner + public health checks
#   env           edit /etc/time-assistant.env (keys, timezone), then restart
#   backup        take a backup now on the Pi
#   pull-backup   copy the newest backup to .\backups\ on this laptop
#   shell         open an SSH shell on the Pi
param([Parameter(Position = 0)][string]$Command = 'status')
. "$PSScriptRoot\_common.ps1"
$cfg = Read-PiConfig
$target = Get-SshTarget $cfg
$dir = $cfg.PI_DIR

switch ($Command) {
    'status' {
        Invoke-Pi $target "systemctl --no-pager --lines=0 status time-assistant | head -12; echo; echo -n 'revision: '; cat '$dir/REVISION' 2>/dev/null || echo unknown; free -h | head -2; df -h / | tail -1"
    }
    'logs'    { ssh -t $target "journalctl -u time-assistant -f -n 50" }
    'restart' { Invoke-Pi $target "sudo systemctl restart time-assistant && sleep 2 && systemctl is-active time-assistant" -Tty }
    'health'  { Invoke-Pi $target "curl -fsS http://127.0.0.1:8000/health; echo; curl -fsS http://127.0.0.1:8001/api/public/profile | head -c 200; echo" }
    'env'     { ssh -t $target "sudo nano /etc/time-assistant.env && sudo systemctl restart time-assistant && echo restarted" }
    'backup'  { Invoke-Pi $target "curl -fsS -X POST http://127.0.0.1:8000/api/backup/run; echo" }
    'pull-backup' {
        $latest = (ssh $target "ls -1t '$dir/data/backups/'time_assistant-*.db 2>/dev/null | head -1").Trim()
        if (-not $latest) { Write-Host "No backups on the Pi yet. Run: deploy\pi.ps1 backup"; exit 1 }
        $local = Join-Path $Root 'backups'
        New-Item -ItemType Directory -Force $local | Out-Null
        scp -q "${target}:$latest" $local
        Write-Host "Copied $(Split-Path -Leaf $latest) to $local" -ForegroundColor Green
    }
    'shell'   { ssh $target }
    default   { Get-Content $PSCommandPath | Select-Object -Skip 1 -First 12 | ForEach-Object { $_ -replace '^# ?', '' } }
}
