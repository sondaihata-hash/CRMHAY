param(
    [string]$TunnelConfig = "$env:USERPROFILE\.cloudflared\config.yml"
)

$ErrorActionPreference = 'Stop'
$cloudflared = (Get-Command cloudflared.exe -ErrorAction SilentlyContinue).Source
if (-not $cloudflared) {
    throw 'Khong tim thay cloudflared.exe trong PATH.'
}
if (-not (Test-Path -LiteralPath $TunnelConfig)) {
    throw "Khong tim thay cau hinh tunnel: $TunnelConfig"
}

$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$shortcutPath = Join-Path $startup 'Cloudflare Tunnel CRMHAY.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $cloudflared
$shortcut.Arguments = "tunnel --config `"$TunnelConfig`" run"
$shortcut.WorkingDirectory = Split-Path -Parent $cloudflared
$shortcut.WindowStyle = 7
$shortcut.Save()

Write-Host "Da cai Cloudflare Tunnel tu khoi dong cung Windows."
Write-Host "Shortcut: $shortcutPath"
Write-Host "Kiem tra domain: https://crmhay.cloud"
