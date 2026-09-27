# Shared helpers for the PowerShell deploy scripts. Dot-sourced; not run directly.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

function Read-PiConfig {
    $file = Join-Path $PSScriptRoot 'pi.config'
    if (-not (Test-Path $file)) {
        Write-Host "deploy/pi.config not found. Copy deploy/pi.config.example to deploy/pi.config and edit it." -ForegroundColor Yellow
        exit 1
    }
    $cfg = @{}
    foreach ($line in Get-Content $file) {
        if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*?)\s*$') { $cfg[$Matches[1]] = $Matches[2] }
    }
    foreach ($k in 'PI_HOST', 'PI_USER', 'PI_DIR', 'PI_ALIAS') {
        if (-not $cfg[$k]) { throw "pi.config is missing $k" }
    }
    return $cfg
}

# Prefer the SSH alias once ssh-setup has run; fall back to user@host.
function Get-SshTarget($cfg) {
    $sshConfig = Join-Path $HOME '.ssh\config'
    if ((Test-Path $sshConfig) -and (Select-String -Path $sshConfig -Pattern "^\s*Host\s+$($cfg.PI_ALIAS)\s*$" -Quiet)) {
        return $cfg.PI_ALIAS
    }
    return "$($cfg.PI_USER)@$($cfg.PI_HOST)"
}

function Invoke-Pi($target, [string]$command, [switch]$Tty) {
    if ($Tty) { ssh -t $target $command } else { ssh $target $command }
    if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE): $command" }
}
