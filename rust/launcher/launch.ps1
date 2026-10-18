# Starts EditorKrab.exe (beside this file) if it is not already serving, then opens it as its own app window.
param([int]$Port = 8871)
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://127.0.0.1:$Port/"
function Up { try { (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false } }
if (-not (Up)) {
    if (-not $env:EDITORKRAB_RUST_STATE) { $env:EDITORKRAB_RUST_STATE = Join-Path $dir 'state.sqlite' }
    $env:EDITORKRAB_RUST_PORT = "$Port"
    $logs = Join-Path $dir 'logs'
    $null = New-Item -ItemType Directory -Force -Path $logs
    Start-Process (Join-Path $dir 'EditorKrab.exe') -WorkingDirectory $dir -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'out.txt') -RedirectStandardError (Join-Path $logs 'err.txt')
    for ($i = 0; $i -lt 40 -and -not (Up); $i++) { Start-Sleep -Milliseconds 250 }
    if (-not (Up)) { throw "EditorKrab did not start on $url; see $logs\err.txt" }
}
# Its own app window (no address bar or tabs) with its own profile, so it never mixes with the person's browser.
# Edge ships with every Windows 10/11; Chrome is the second choice.
$window = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
            "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
            "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $window) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show('EditorKrab needs Microsoft Edge or Google Chrome to open its window.', 'EditorKrab') | Out-Null
    throw 'Neither Microsoft Edge nor Google Chrome was found.'
}
$windowProfile = Join-Path $env:LOCALAPPDATA 'EditorKrab\window'
Start-Process $window -ArgumentList "--app=$url", '--window-size=1320,860', "--user-data-dir=`"$windowProfile`"", '--no-first-run'
