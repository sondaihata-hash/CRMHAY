$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pollSeconds = 5
$settleSeconds = 3
$retrySeconds = 15

while ($true) {
    $status = git status --porcelain
    if ($status) {
        Start-Sleep -Seconds $settleSeconds
        $statusAfterSettle = git status --porcelain
        if (-not $statusAfterSettle) {
            continue
        }

        $branch = (git branch --show-current).Trim()
        if (-not $branch) {
            Write-Error 'Automatic push stopped: repository is in detached HEAD state.'
            exit 1
        }

        $message = "chore: auto-save changes $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        git add --all
        git commit -m $message
        if ($LASTEXITCODE -ne 0) {
            Write-Error 'Automatic push stopped: git commit failed.'
            exit 1
        }

        git push origin $branch
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Automatic push failed. Retrying in $retrySeconds seconds. Check GitHub authentication or network access."
            Start-Sleep -Seconds $retrySeconds
            continue
        }
    }

    Start-Sleep -Seconds $pollSeconds
}