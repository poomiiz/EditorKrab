# CI check of an unzipped Windows download: install it into a temporary folder, then start the installed exe and load its page.
param([Parameter(Mandatory = $true)][string]$Folder)
$ErrorActionPreference = 'Stop'
$target = Join-Path ([IO.Path]::GetTempPath()) ('EditorKrab-install-' + [Guid]::NewGuid())
& (Join-Path $Folder 'install.ps1') -InstallRoot $target
foreach ($name in 'EditorKrab.exe', 'tools\ffmpeg\ffprobe.exe', 'tools\ffmpeg\ffmpeg.exe', 'tools\ffmpeg\LICENSE') {
    if (-not (Test-Path -LiteralPath (Join-Path $target $name))) { throw "Installed copy is missing $name" }
}
$link = Join-Path ([Environment]::GetFolderPath('Programs')) 'EditorKrab.lnk'
if (-not (Test-Path -LiteralPath $link)) { throw 'Start Menu shortcut was not created' }
# Capture all output first: Select-Object -First 1 in the pipeline would stop ffprobe early and fail its exit code.
$version = & (Join-Path $target 'tools\ffmpeg\ffprobe.exe') -version
if ($LASTEXITCODE -ne 0) { throw 'Bundled ffprobe does not run' }
Write-Host $version[0]
$env:EDITORKRAB_RUST_STATE = Join-Path $target 'check.sqlite'
$env:EDITORKRAB_RUST_PORT = '8899'
$server = Start-Process (Join-Path $target 'EditorKrab.exe') -WorkingDirectory $target -PassThru -WindowStyle Hidden
try {
    $ok = $false
    for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
        Start-Sleep -Milliseconds 500
        try { $ok = (Invoke-WebRequest 'http://127.0.0.1:8899/' -UseBasicParsing -TimeoutSec 2).Content -match 'EditorKrab' } catch { }
    }
    if (-not $ok) { throw 'Installed EditorKrab.exe did not serve its page on port 8899' }
    Write-Host 'PASS package installs, shortcut created, bundled ffprobe runs, installed exe serves its page'
} finally { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
