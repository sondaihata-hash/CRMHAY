param(
    [Parameter(Mandatory = $true)]
    [string]$ApkPath,
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [int]$VersionCode = 0
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$source = (Resolve-Path -LiteralPath $ApkPath).Path
$downloadDirectory = Join-Path $repoRoot 'downloads'
$target = Join-Path $downloadDirectory 'crmhay-mobile.apk'

if ([IO.Path]::GetExtension($source).ToLowerInvariant() -ne '.apk') {
    throw 'ApkPath phai tro den mot file .apk.'
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw 'Version phai co dang X.Y.Z, vi du 1.4.1.'
}
if ($VersionCode -lt 0) {
    throw 'VersionCode khong duoc am.'
}

New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $target -Force

$release = [ordered]@{
    version = $Version
    version_code = $VersionCode
    download_url = 'https://crmhay.cloud/downloads/crmhay-mobile.apk'
    published_at = (Get-Date).ToUniversalTime().ToString('o')
}
$release | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $downloadDirectory 'mobile-release.json') -Encoding utf8

[Environment]::SetEnvironmentVariable('CRM_MOBILE_VERSION', $Version, 'User')
if ($VersionCode -gt 0) {
    [Environment]::SetEnvironmentVariable('CRM_MOBILE_VERSION_CODE', "$VersionCode", 'User')
}

Write-Host "Da phat hanh CRM Mobile $Version ($VersionCode)."
Write-Host "APK: $target"
Write-Host 'Khoi dong lai CRM de nap phien ban moi neu supervisor dang chay.'
