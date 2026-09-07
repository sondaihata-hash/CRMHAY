param(
    [int]$Port = 5000,
    [int]$PollSeconds = 5
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "Chua co .venv. Chay: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

if (-not $env:CRM_SECRET_KEY -or $env:CRM_SECRET_KEY.Length -lt 32) {
    throw 'Can dat CRM_SECRET_KEY dai it nhat 32 ky tu trong bien moi truong Windows.'
}

$env:FLASK_ENV = 'production'
$env:PORT = $Port
if (-not $env:CRM_USE_CELERY) {
    $env:CRM_USE_CELERY = 'false'
}
$persistentMobileVersion = [Environment]::GetEnvironmentVariable('CRM_MOBILE_VERSION', 'User')
if ($persistentMobileVersion) {
    $env:CRM_MOBILE_VERSION = $persistentMobileVersion
}
$persistentMobileVersionCode = [Environment]::GetEnvironmentVariable('CRM_MOBILE_VERSION_CODE', 'User')
if ($persistentMobileVersionCode) {
    $env:CRM_MOBILE_VERSION_CODE = $persistentMobileVersionCode
}

$logDirectory = Join-Path $env:TEMP 'crmh-supervisor'
$logPath = Join-Path $logDirectory 'vps-supervisor.log'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Start-Transcript -Path $logPath -Append | Out-Null

function Get-SourceSignature {
    $files = git ls-files
    if ($LASTEXITCODE -ne 0) {
        throw 'Khong the doc danh sach file Git de theo doi cap nhat.'
    }

    $parts = foreach ($file in $files) {
        $path = Join-Path $repoRoot $file
        if (Test-Path $path) {
            $item = Get-Item $path
            "$file|$($item.Length)|$($item.LastWriteTimeUtc.Ticks)"
        }
    }

    return ($parts -join "`n")
}

function Start-CrmProcess {
    Write-Host "$(Get-Date -Format s) Starting CRM on 127.0.0.1:$Port"
    return Start-Process `
        -FilePath $python `
        -ArgumentList @('-m', 'waitress', "--listen=127.0.0.1:$Port", 'app:app') `
        -WorkingDirectory $repoRoot `
        -PassThru
}

$signature = Get-SourceSignature
$process = $null

try {
    while ($true) {
        if (-not $process -or $process.HasExited) {
            if ($process) {
                Write-Warning "$(Get-Date -Format s) CRM stopped with exit code $($process.ExitCode); restarting."
            }
            $process = Start-CrmProcess
            $signature = Get-SourceSignature
        }

        Start-Sleep -Seconds $PollSeconds
        $newSignature = Get-SourceSignature
        if ($newSignature -ne $signature) {
            Write-Host "$(Get-Date -Format s) Source change detected; restarting CRM."
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit()
            $process = Start-CrmProcess
            $signature = $newSignature
        }
    }
}
finally {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Stop-Transcript | Out-Null
}
