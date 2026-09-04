param(
    [int]$Port = 5000
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Error 'Chua co .venv. Chay: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
}

if (-not $env:CRM_SECRET_KEY -or $env:CRM_SECRET_KEY.Length -lt 32) {
    Write-Error 'Can dat CRM_SECRET_KEY dai it nhat 32 ky tu truoc khi chay production.'
}

$env:FLASK_ENV = 'production'
$env:PORT = $Port
if (-not $env:CRM_USE_CELERY) {
    $env:CRM_USE_CELERY = 'false'
}
& '.venv\Scripts\python.exe' -m waitress --listen=127.0.0.1:$Port app:app
