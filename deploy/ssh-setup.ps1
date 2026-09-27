# One-time: create an SSH key on this laptop, install it on the Pi, and add an "ssh timepi" alias.
#   powershell -ExecutionPolicy Bypass -File deploy\ssh-setup.ps1
# You'll type the Pi password once; after that, no passwords.
. "$PSScriptRoot\_common.ps1"
$cfg = Read-PiConfig

if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Write-Host "OpenSSH client not found. Settings > System > Optional features > add 'OpenSSH Client'." -ForegroundColor Yellow
    exit 1
}

$sshDir = Join-Path $HOME '.ssh'
New-Item -ItemType Directory -Force $sshDir | Out-Null
$key = Join-Path $sshDir "id_ed25519_$($cfg.PI_ALIAS)"

if (-not (Test-Path $key)) {
    Write-Host "Creating key $key"
    ssh-keygen -t ed25519 -f $key -N '""' -C "time-assistant-deploy" | Out-Null
} else {
    Write-Host "Key already exists: $key"
}

Write-Host "Installing the public key on $($cfg.PI_USER)@$($cfg.PI_HOST) (enter the Pi password if asked)"
$pub = (Get-Content "$key.pub" -Raw).Trim()
ssh "$($cfg.PI_USER)@$($cfg.PI_HOST)" "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; grep -qxF '$pub' ~/.ssh/authorized_keys || echo '$pub' >> ~/.ssh/authorized_keys"
if ($LASTEXITCODE -ne 0) { throw "Could not install the key. Is SSH enabled on the Pi (sudo raspi-config > Interface Options > SSH)?" }

$sshConfig = Join-Path $sshDir 'config'
if (-not (Test-Path $sshConfig)) { New-Item -ItemType File $sshConfig | Out-Null }
if (-not (Select-String -Path $sshConfig -Pattern "^\s*Host\s+$($cfg.PI_ALIAS)\s*$" -Quiet)) {
    Add-Content -Path $sshConfig -Encoding ascii -Value @"

Host $($cfg.PI_ALIAS)
    HostName $($cfg.PI_HOST)
    User $($cfg.PI_USER)
    IdentityFile $key
    IdentitiesOnly yes
    ServerAliveInterval 30
"@
    Write-Host "Added 'Host $($cfg.PI_ALIAS)' to $sshConfig"
}

ssh -o BatchMode=yes $cfg.PI_ALIAS "echo connected to \$(hostname) as \$(whoami)"
if ($LASTEXITCODE -eq 0) { Write-Host "Done. Try: ssh $($cfg.PI_ALIAS)" -ForegroundColor Green }
else { Write-Host "Key login failed; check the output above." -ForegroundColor Yellow }
