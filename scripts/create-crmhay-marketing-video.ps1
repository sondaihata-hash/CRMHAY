param(
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$ffmpeg = 'C:\ffmpeg\bin\ffmpeg.exe'
if (-not (Test-Path $ffmpeg)) {
    throw "Khong tim thay ffmpeg tai $ffmpeg"
}

if (-not $OutputPath) {
    $OutputPath = Join-Path $repoRoot 'downloads\crmhay-facebook-promo.mp4'
}

$temp = Join-Path $env:TEMP ('crmhay-video-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp -Force | Out-Null
try {
    $scenes = @(
        @{
            Name = 'scene1.mp4'
            Color = '073b4c'
            Lines = @('CRM HAY', 'Quan ly khach hang', 'va don hang tap trung')
        },
        @{
            Name = 'scene2.mp4'
            Color = '1261a0'
            Lines = @('Khong bo sot khach hang', 'Theo doi Sales ro rang', 'Biet doanh thu thuoc ve ai')
        },
        @{
            Name = 'scene3.mp4'
            Color = '087f5b'
            Lines = @('Dung thu CRM HAY ngay', 'Tu 149.500d/thang', 'crmhay.cloud')
        }
    )

    $scenePaths = @()
    foreach ($scene in $scenes) {
        $textPaths = @()
        foreach ($line in $scene.Lines) {
            $textPath = Join-Path $temp ([guid]::NewGuid().ToString('N') + '.txt')
            [System.IO.File]::WriteAllText($textPath, $line, (New-Object System.Text.UTF8Encoding($false)))
            $textPaths += $textPath
        }
        $output = Join-Path $temp $scene.Name
        $filterPath = {
            param($path)
            return $path.Replace('\', '/').Replace(':', '\:')
        }
        $filter = @(
            "drawtext=fontfile='C\:/Windows/Fonts/arial.ttf':textfile='$(&$filterPath $textPaths[0])':fontcolor=white:fontsize=104:x=(w-text_w)/2:y=470",
            "drawtext=fontfile='C\:/Windows/Fonts/arial.ttf':textfile='$(&$filterPath $textPaths[1])':fontcolor=white:fontsize=62:x=(w-text_w)/2:y=720",
            "drawtext=fontfile='C\:/Windows/Fonts/arial.ttf':textfile='$(&$filterPath $textPaths[2])':fontcolor=white:fontsize=62:x=(w-text_w)/2:y=840",
            "fade=t=in:st=0:d=0.5,fade=t=out:st=5.5:d=0.5"
        ) -join ','
        & $ffmpeg -y -f lavfi -i "color=c=$($scene.Color):s=1080x1920:r=30:d=6" -vf $filter -an -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p $output
        if ($LASTEXITCODE -ne 0) {
            throw "Khong the tao $($scene.Name)"
        }
        $scenePaths += $output
    }

    $listPath = Join-Path $temp 'concat.txt'
    $concatLines = $scenePaths | ForEach-Object { "file '$($_.Replace('\','/'))'" }
    [System.IO.File]::WriteAllLines($listPath, $concatLines, [System.Text.Encoding]::ASCII)
    & $ffmpeg -y -f concat -safe 0 -i $listPath -c copy $OutputPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Khong the ghep video marketing.'
    }
    Write-Host "Da tao video: $OutputPath"
}
finally {
    if (Test-Path $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force
    }
}
