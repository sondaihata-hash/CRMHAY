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
try {
    $voiceText = @'
Bạn đang mất khách vì tin nhắn bị trôi và đơn hàng bị bỏ sót?
CRM HAY giúp bạn quản lý khách hàng, Sales và đơn hàng trên một màn hình.
Theo dõi lịch sử chăm sóc, phân công khách hàng và biết doanh thu thuộc về ai.
Tạo đơn hàng, thanh toán QR và gửi thông tin sản xuất nhanh chóng.
Hãy dùng thử CRM HAY ngay hôm nay tại crmhay.cloud.
Gói Basic chỉ từ một trăm bốn mươi chín nghìn năm trăm đồng mỗi tháng.
'@
    $voicePath = Join-Path $temp 'voice.mp3'
    & $edgeTts --voice vi-VN-HoaiMyNeural --text $voiceText --write-media $voicePath
    if ($LASTEXITCODE -ne 0) { throw 'Khong the tao giong doc tieng Viet.' }

    $sceneData = @(
        @{ Color='082f49'; Title='CRM HAY'; Subtitle='Quản lý khách hàng thông minh'; Detail='Không bỏ sót cơ hội bán hàng' },
        @{ Color='164e63'; Title='KHÁCH HÀNG'; Subtitle='Hồ sơ và lịch sử chăm sóc'; Detail='Tập trung dữ liệu khách hàng trên một màn hình' },
        @{ Color='1e3a8a'; Title='ĐƠN HÀNG'; Subtitle='Tạo đơn, thanh toán QR'; Detail='Theo dõi trạng thái và gửi sản xuất nhanh chóng' },
        @{ Color='14532d'; Title='DOANH THU SALES'; Subtitle='Phân quyền và báo cáo rõ ràng'; Detail='Biết chính xác doanh thu thuộc về ai' },
        @{ Color='7c2d12'; Title='BẮT ĐẦU NGAY'; Subtitle='Dùng thử CRM HAY'; Detail='crmhay.cloud  |  Từ 149.500đ/tháng' }
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
