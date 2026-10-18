# Copies this unpacked EditorKrab folder to the user's programs folder and adds Desktop and Start Menu shortcuts.
param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\EditorKrab'))
$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
foreach ($name in 'EditorKrab.exe', 'EditorKrab.vbs', 'launch.ps1', 'EditorKrab.ico', 'tools\ffmpeg\ffmpeg.exe', 'tools\ffmpeg\ffprobe.exe') {
    if (-not (Test-Path -LiteralPath (Join-Path $source $name))) { throw "This EditorKrab package is incomplete: $name is missing. Unzip the whole download, then run Install again." }
}
if ($source.TrimEnd('\') -ieq $InstallRoot.TrimEnd('\')) { throw 'Run Install from the unzipped download, not from the installed folder.' }
# The server keeps running after its window closes; stop the installed copy so its files can be replaced.
$running = Get-Process -Name EditorKrab -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase) }
if ($running) { Write-Host 'Closing the running EditorKrab...'; $running | Stop-Process -Force; $running | Wait-Process -Timeout 10 }
$null = New-Item -ItemType Directory -Force -Path $InstallRoot
# Only files in this package are replaced: state.sqlite and logs beside the exe stay, and the speech model lives in %LOCALAPPDATA%\EditorKrab.
Copy-Item -Path (Join-Path $source '*') -Destination $InstallRoot -Recurse -Force
$shell = New-Object -ComObject WScript.Shell
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'EditorKrab.lnk'
$desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'EditorKrab.lnk'
foreach ($path in $startMenu, $desktop) {
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $link.Arguments = '"' + (Join-Path $InstallRoot 'EditorKrab.vbs') + '"'
    $link.WorkingDirectory = $InstallRoot
    $link.IconLocation = Join-Path $InstallRoot 'EditorKrab.ico'
    $link.Save()
}
Write-Host "EditorKrab is installed in $InstallRoot. Open it from the Desktop or Start Menu."
