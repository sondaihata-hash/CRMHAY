param(
    [int]$Port = 5000
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$supervisor = Join-Path $PSScriptRoot 'run-vps-supervisor.ps1'
$taskName = 'CRMHAY VPS'
$powershell = (Get-Command powershell.exe).Source

if (-not (Test-Path $supervisor)) {
    throw "Khong tim thay $supervisor"
}

$action = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$supervisor`" -Port $Port" `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force `
        -ErrorAction Stop | Out-Null

    if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
        throw "Khong the xac minh task '$taskName' sau khi cai dat."
    }

    Write-Host "Da cai task '$taskName'. CRM se tu chay moi khi tai khoan Windows dang nhap."
    Write-Host "Kiem tra: Get-ScheduledTask -TaskName '$taskName'"
    Write-Host "Chay ngay: Start-ScheduledTask -TaskName '$taskName'"
}
catch {
    $startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    $shortcutPath = Join-Path $startup 'CRMHAY VPS.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $powershell
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$supervisor`" -Port $Port"
    $shortcut.WorkingDirectory = $repoRoot
    $shortcut.Save()
    Write-Warning "Khong co quyen tao Scheduled Task; da tao shortcut trong Startup."
    Write-Host "Shortcut: $shortcutPath"
}
