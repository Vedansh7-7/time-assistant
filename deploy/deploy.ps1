# Deploy the committed code to the Pi and restart the service.
#   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1            # tests, then deploy HEAD
#   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -SkipTests
#
# Ships exactly what is committed (git archive HEAD). The Pi's data/ (database, backups)
# and .venv/ are never touched. install.sh backs up the database before restarting.
param([switch]$SkipTests, [switch]$AllowDirty)
. "$PSScriptRoot\_common.ps1"
$cfg = Read-PiConfig
$target = Get-SshTarget $cfg
Set-Location $Root

$dirty = git status --porcelain
if ($dirty -and -not $AllowDirty) {
    Write-Host "You have uncommitted changes; only committed code is deployed." -ForegroundColor Yellow
    Write-Host "Commit first, or re-run with -AllowDirty to deploy the last commit anyway."
    exit 1
}

if (-not $SkipTests) {
    Write-Host "Running tests" -ForegroundColor Cyan
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    & $py -m pytest -q --no-header -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { Write-Host "Tests failed; not deploying." -ForegroundColor Red; exit 1 }
}

$rev = (git rev-parse --short HEAD).Trim()
$tar = Join-Path $env:TEMP "time-assistant-$rev.tar"
Write-Host "Packing $rev" -ForegroundColor Cyan
git archive --format=tar -o $tar HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }

Write-Host "Uploading to $target" -ForegroundColor Cyan
scp -q $tar "${target}:/tmp/time-assistant.tar"
if ($LASTEXITCODE -ne 0) { throw "Upload failed. Can you 'ssh $target'?" }
Remove-Item $tar

$dir = $cfg.PI_DIR
# Replace code directories (so deleted files disappear), keep data/ and .venv/.
$remote = @"
set -e
mkdir -p '$dir'
cd '$dir'
rm -rf app tests deploy
tar -xf /tmp/time-assistant.tar -C '$dir'
rm -f /tmp/time-assistant.tar
echo '$rev' > REVISION
bash deploy/install.sh
"@
Write-Host "Installing on the Pi" -ForegroundColor Cyan
Invoke-Pi $target ($remote -replace "`r", '') -Tty  # tty: sudo may ask for the password

Write-Host "Checking health" -ForegroundColor Cyan
Invoke-Pi $target "for i in 1 2 3 4 5 6 7 8 9 10; do curl -fsS http://127.0.0.1:8000/health && exit 0; sleep 2; done; echo 'not healthy'; journalctl -u time-assistant -n 30 --no-pager; exit 1"
Write-Host "`nDeployed $rev. Open http://$($cfg.PI_HOST):8000" -ForegroundColor Green
