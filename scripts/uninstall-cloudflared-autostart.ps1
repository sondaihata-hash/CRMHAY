$ErrorActionPreference = 'Stop'
$shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Cloudflare Tunnel CRMHAY.lnk'

if (Test-Path -LiteralPath $shortcut) {
    Remove-Item -LiteralPath $shortcut -Force
    Write-Host 'Da go Cloudflare Tunnel khoi Startup.'
}
else {
    Write-Host 'Khong tim thay shortcut Cloudflare Tunnel.'
}
