$ErrorActionPreference = 'Stop'
$taskName = 'CRMHAY VPS'
$shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\CRMHAY VPS.lnk'

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    Write-Host "Da go task '$taskName'."
}
if (Test-Path $shortcut) {
    Remove-Item -LiteralPath $shortcut -Force
    Write-Host "Da go shortcut Startup."
}
