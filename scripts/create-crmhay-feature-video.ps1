param(
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$ffmpeg = 'C:\ffmpeg\bin\ffmpeg.exe'
$python = (Get-Command python).Source
if (-not (Test-Path $ffmpeg)) { throw "Khong tim thay ffmpeg tai $ffmpeg" }
if (-not $OutputPath) { $OutputPath = Join-Path $repoRoot 'downloads\crmhay-facebook-features.mp4' }

$screenshot = Join-Path $repoRoot 'downloads\marketing-screen-1.png'
if (-not (Test-Path $screenshot)) {
    throw "Khong tim thay anh giao dien $screenshot"
}

$temp = Join-Path $env:TEMP ('crmhay-feature-video-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp -Force | Out-Null
$U = { param($value) [regex]::Unescape($value) }
try {
    $voiceText = Join-Path $temp 'voice.txt'
    [IO.File]::WriteAllText($voiceText, @'
Bạn đang mất khách vì tin nhắn bị trôi và đơn hàng bị bỏ sót.
CRM HAY giúp bạn quản lý khách hàng, Sales và đơn hàng trên một màn hình.
Bạn có thể tập trung dữ liệu khách hàng, theo dõi lịch sử chăm sóc và phân công khách hàng.
Hệ thống hỗ trợ tạo đơn hàng, thanh toán QR và theo dõi trạng thái sản xuất.
Báo cáo doanh thu giúp bạn biết chính xác Sales nào đang tạo ra kết quả.
Hãy dùng thử CRM HAY ngay hôm nay tại crmhay.cloud.
'@, (New-Object Text.UTF8Encoding($false)))
    $voice = Join-Path $temp 'voice.mp3'
    & $python (Join-Path $PSScriptRoot 'generate-vietnamese-voice.py') $voiceText $voice
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $voice) -or (Get-Item $voice).Length -lt 10000) {
        throw 'Khong tao duoc file giong doc tieng Viet.'
    }

    $crops = @()
    foreach ($y in @(0, 2100, 4200, 6300)) {
        $crop = Join-Path $temp "crop-$y.png"
        & $ffmpeg -y -i $screenshot -vf "crop=663:1900:0:$y" $crop
        if ($LASTEXITCODE -ne 0) { throw "Khong cat duoc anh giao dien tai $y" }
        $crops += $crop
    }

    $titles = @(
        (&$U 'T\u1ed5ng quan CRM'),
        (&$U 'T\u1eadp trung kh\u00e1ch h\u00e0ng'),
        (&$U '\u0110\u01a1n h\u00e0ng v\u00e0 doanh thu'),
        (&$U 'B\u1eaft \u0111\u1ea7u v\u1edbi CRM HAY')
    )
    $subtitles = @(
        (&$U 'Theo d\u00f5i m\u1ecdi c\u01a1 h\u1ed9i b\u00e1n h\u00e0ng'),
        (&$U 'L\u1ecbch s\u1eed ch\u0103m s\u00f3c tr\u00ean m\u1ed9t m\u00e0n h\u00ecnh'),
        (&$U 'Sales, thanh to\u00e1n v\u00e0 b\u00e1o c\u00e1o r\u00f5 r\u00e0ng'),
        (&$U 'crmhay.cloud | D\u00f9ng th\u1eed ngay')
    )
    $scenes = @()
    for ($i = 0; $i -lt $crops.Count; $i++) {
        $titlePath = Join-Path $temp "title-$i.txt"
        $subtitlePath = Join-Path $temp "subtitle-$i.txt"
        [IO.File]::WriteAllText($titlePath, $titles[$i], (New-Object Text.UTF8Encoding($false)))
        [IO.File]::WriteAllText($subtitlePath, $subtitles[$i], (New-Object Text.UTF8Encoding($false)))
        $scene = Join-Path $temp "scene-$i.mp4"
        $titleEsc = $titlePath.Replace('\', '/').Replace(':', '\:')
        $subtitleEsc = $subtitlePath.Replace('\', '/').Replace(':', '\:')
        $filter = "scale=1080:-2,crop=1080:1920,drawbox=x=0:y=0:w=1080:h=300:color=073b4c@0.92:t=fill,drawtext=fontfile='C\:/Windows/Fonts/arial.ttf':textfile='$titleEsc':fontcolor=white:fontsize=62:x=(w-text_w)/2:y=90,drawtext=fontfile='C\:/Windows/Fonts/arial.ttf':textfile='$subtitleEsc':fontcolor=white:fontsize=34:x=(w-text_w)/2:y=205"
        & $ffmpeg -y -loop 1 -i $crops[$i] -t 7 -vf $filter -an -c:v libx264 -pix_fmt yuv420p $scene
        if ($LASTEXITCODE -ne 0) { throw "Khong tao duoc canh $i" }
        $scenes += $scene
    }

    $list = Join-Path $temp 'concat.txt'
    [IO.File]::WriteAllLines($list, ($scenes | ForEach-Object { "file '$($_.Replace('\','/'))'" }), [Text.Encoding]::ASCII)
    $silent = Join-Path $temp 'silent.mp4'
    & $ffmpeg -y -f concat -safe 0 -i $list -c copy $silent
    if ($LASTEXITCODE -ne 0) { throw 'Khong ghep duoc cac canh.' }
    & $ffmpeg -y -i $silent -i $voice -filter_complex "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]" -map 0:v:0 -map "[a]" -c:v copy -c:a aac -ar 48000 -ac 2 -b:a 160k -shortest $OutputPath
    if ($LASTEXITCODE -ne 0) { throw 'Khong ghep duoc giong doc vao video.' }
    Write-Host "Da tao video: $OutputPath"
}
finally {
    if (Test-Path $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
}
