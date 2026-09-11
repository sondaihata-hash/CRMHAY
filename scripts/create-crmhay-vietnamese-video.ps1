param(
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$ffmpeg = 'C:\ffmpeg\bin\ffmpeg.exe'
$edgeTts = (Get-Command edge-tts -ErrorAction SilentlyContinue).Source
if (-not (Test-Path $ffmpeg)) { throw "Khong tim thay ffmpeg tai $ffmpeg" }
if (-not $edgeTts) { throw 'Khong tim thay edge-tts de tao giong doc tieng Viet.' }
if (-not $OutputPath) { $OutputPath = Join-Path $repoRoot 'downloads\crmhay-facebook-vietnamese.mp4' }

$temp = Join-Path $env:TEMP ('crmhay-vietnamese-video-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp -Force | Out-Null
$U = { param($value) [regex]::Unescape($value) }
try {
    $voiceText = @(
        (&$U 'B\u1ea1n \u0111ang m\u1ea5t kh\u00e1ch v\u00ec tin nh\u1eafn b\u1ecb tr\u00f4i v\u00e0 \u0111\u01a1n h\u00e0ng b\u1ecb b\u1ecf s\u00f3t?'),
        (&$U 'CRM HAY gi\u00fap b\u1ea1n qu\u1ea3n l\u00fd kh\u00e1ch h\u00e0ng, Sales v\u00e0 \u0111\u01a1n h\u00e0ng tr\u00ean m\u1ed9t m\u00e0n h\u00ecnh.'),
        (&$U 'Theo d\u00f5i l\u1ecbch s\u1eed ch\u0103m s\u00f3c, ph\u00e2n c\u00f4ng kh\u00e1ch h\u00e0ng v\u00e0 bi\u1ebft doanh thu thu\u1ed9c v\u1ec1 ai.'),
        (&$U 'T\u1ea1o \u0111\u01a1n h\u00e0ng, thanh to\u00e1n QR v\u00e0 g\u1eedi th\u00f4ng tin s\u1ea3n xu\u1ea5t nhanh ch\u00f3ng.'),
        (&$U 'H\u00e3y d\u00f9ng th\u1eed CRM HAY ngay h\u00f4m nay t\u1ea1i crmhay.cloud.'),
        (&$U 'G\u00f3i Basic ch\u1ec9 t\u1eeb m\u1ed9t tr\u0103m b\u1ed1n m\u01b0\u01a1i ch\u00edn ngh\u00ecn n\u0103m tr\u0103m \u0111\u1ed3ng m\u1ed7i th\u00e1ng.')
    ) -join "`n"
    $voicePath = Join-Path $temp 'voice.mp3'
    & $edgeTts --voice vi-VN-HoaiMyNeural --text $voiceText --write-media $voicePath
    if ($LASTEXITCODE -ne 0) { throw 'Khong the tao giong doc tieng Viet.' }

    $sceneData = @(
        @{ Color='082f49'; Title='CRM HAY'; Subtitle=(&$U 'Qu\u1ea3n l\u00fd kh\u00e1ch h\u00e0ng th\u00f4ng minh'); Detail=(&$U 'Kh\u00f4ng b\u1ecf s\u00f3t c\u01a1 h\u1ed9i b\u00e1n h\u00e0ng') },
        @{ Color='164e63'; Title=(&$U 'KH\u00c1CH H\u00c0NG'); Subtitle=(&$U 'H\u1ed3 s\u01a1 v\u00e0 l\u1ecbch s\u1eed ch\u0103m s\u00f3c'); Detail=(&$U 'T\u1eadp trung d\u1eef li\u1ec7u kh\u00e1ch h\u00e0ng tr\u00ean m\u1ed9t m\u00e0n h\u00ecnh') },
        @{ Color='1e3a8a'; Title=(&$U '\u0110\u01a0N H\u00c0NG'); Subtitle=(&$U 'T\u1ea1o \u0111\u01a1n, thanh to\u00e1n QR'); Detail=(&$U 'Theo d\u00f5i tr\u1ea1ng th\u00e1i v\u00e0 g\u1eedi s\u1ea3n xu\u1ea5t nhanh ch\u00f3ng') },
        @{ Color='14532d'; Title='DOANH THU SALES'; Subtitle=(&$U 'Ph\u00e2n quy\u1ec1n v\u00e0 b\u00e1o c\u00e1o r\u00f5 r\u00e0ng'); Detail=(&$U 'Bi\u1ebft ch\u00ednh x\u00e1c doanh thu thu\u1ed9c v\u1ec1 ai') },
        @{ Color='7c2d12'; Title=(&$U 'B\u1eaeT \u0110\u1ea6U NGAY'); Subtitle=(&$U 'D\u00f9ng th\u1eed CRM HAY'); Detail=(&$U 'crmhay.cloud  |  T\u1eeb 149.500\u0111/th\u00e1ng') }
    )
    $scenePaths = @()
    $filterPath = {
        param($path)
        return $path.Replace('\', '/').Replace(':', '\:')
    }
    $font = 'C\:/Windows/Fonts/arial.ttf'
    foreach ($index in 0..($sceneData.Count - 1)) {
        $scene = $sceneData[$index]
        $texts = @($scene.Title, $scene.Subtitle, $scene.Detail)
        $textPaths = @()
        foreach ($text in $texts) {
            $textPath = Join-Path $temp ([guid]::NewGuid().ToString('N') + '.txt')
            [System.IO.File]::WriteAllText($textPath, $text, (New-Object System.Text.UTF8Encoding($false)))
            $textPaths += $textPath
        }
        $output = Join-Path $temp ("scene-$index.mp4")
        $filter = @(
            "drawbox=x=90:y=250:w=900:h=1420:color=white@0.10:t=6",
            "drawbox=x=150:y=420:w=780:h=260:color=white@0.16:t=0",
            "drawbox=x=150:y=760:w=780:h=210:color=white@0.16:t=0",
            "drawbox=x=150:y=1040:w=780:h=210:color=white@0.16:t=0",
            "drawtext=fontfile='$font':textfile='$(&$filterPath $textPaths[0])':fontcolor=white:fontsize=94:x=(w-text_w)/2:y=310",
            "drawtext=fontfile='$font':textfile='$(&$filterPath $textPaths[1])':fontcolor=white:fontsize=50:x=(w-text_w)/2:y=500",
            "drawtext=fontfile='$font':textfile='$(&$filterPath $textPaths[2])':fontcolor=white:fontsize=42:x=(w-text_w)/2:y=820",
            "drawtext=fontfile='$font':text='CRM HAY':fontcolor=white@0.70:fontsize=34:x=150:y=1370",
            "drawtext=fontfile='$font':text='crmhay.cloud':fontcolor=white@0.90:fontsize=38:x=(w-text_w)/2:y=1530",
            "fade=t=in:st=0:d=0.4,fade=t=out:st=4.6:d=0.4"
        ) -join ','
        & $ffmpeg -y -f lavfi -i "color=c=$($scene.Color):s=1080x1920:r=30:d=5" -vf $filter -an -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p $output
        if ($LASTEXITCODE -ne 0) { throw "Khong the tao canh $index" }
        $scenePaths += $output
    }

    $listPath = Join-Path $temp 'concat.txt'
    [System.IO.File]::WriteAllLines(
        $listPath,
        ($scenePaths | ForEach-Object { "file '$($_.Replace('\','/'))'" }),
        [System.Text.Encoding]::ASCII
    )
    $silentVideo = Join-Path $temp 'silent.mp4'
    & $ffmpeg -y -f concat -safe 0 -i $listPath -c copy $silentVideo
    if ($LASTEXITCODE -ne 0) { throw 'Khong the ghep cac canh video.' }
    & $ffmpeg -y -i $silentVideo -i $voicePath -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 128k -shortest $OutputPath
    if ($LASTEXITCODE -ne 0) { throw 'Khong the ghep am thanh vao video.' }
    Write-Host "Da tao video: $OutputPath"
}
finally {
    if (Test-Path $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
}
