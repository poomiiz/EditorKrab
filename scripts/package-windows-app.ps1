# Builds the Windows download: EditorKrab.exe, its launcher, ffmpeg and the installer, zipped. Publishes nothing.
# The speech engine and model are not bundled; the app downloads them on first use (rust/src/asr_download.rs).
param([Parameter(Mandatory = $true)][string]$Exe, [Parameter(Mandatory = $true)][string]$Output)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$ffmpegUrl = 'https://github.com/GyanD/codexffmpeg/releases/download/7.1/ffmpeg-7.1-essentials_build.zip'
$ffmpegSha256 = 'fa7d4d7e795db0e2503f49f105f46ed5852386f0cfdd819899be3b65ebde24fc'
if (-not (Test-Path -LiteralPath $Exe)) { throw "EditorKrab.exe not found: $Exe. Run cargo build --release first." }
$work = Join-Path ([IO.Path]::GetTempPath()) ('EditorKrab-package-' + [Guid]::NewGuid())
$app = Join-Path $work 'EditorKrab'
try {
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $app 'tools\ffmpeg')
    Copy-Item -LiteralPath $Exe -Destination (Join-Path $app 'EditorKrab.exe')
    foreach ($name in 'EditorKrab.vbs', 'launch.ps1', 'install.ps1', 'Install EditorKrab.cmd') {
        Copy-Item -LiteralPath (Join-Path $root "rust\launcher\$name") -Destination $app
    }
    Copy-Item -LiteralPath (Join-Path $root 'rust\data\editorkrab-crab.ico') -Destination (Join-Path $app 'EditorKrab.ico')
    Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination $app
    Copy-Item -LiteralPath (Join-Path $root 'THIRD_PARTY_NOTICES.md') -Destination $app
    $zip = Join-Path $work 'ffmpeg.zip'
    Invoke-WebRequest -Uri $ffmpegUrl -OutFile $zip -UseBasicParsing
    $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $ffmpegSha256) { throw "ffmpeg download checksum mismatch: $actual" }
    Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $work 'ffmpeg')
    $bin = Join-Path $work 'ffmpeg\ffmpeg-7.1-essentials_build'
    foreach ($name in 'bin\ffmpeg.exe', 'bin\ffprobe.exe', 'LICENSE') {
        Copy-Item -LiteralPath (Join-Path $bin $name) -Destination (Join-Path $app 'tools\ffmpeg')
    }
    $Output = [IO.Path]::GetFullPath($Output)
    $null = New-Item -ItemType Directory -Force -Path (Split-Path $Output -Parent)
    if (Test-Path -LiteralPath $Output) { throw "Output already exists: $Output" }
    Compress-Archive -Path $app -DestinationPath $Output
    Write-Host ("SHA256: " + (Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash.ToLower())
    Write-Host "Package: $Output"
} finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
