# Build locally; this command does not publish or install anything.
param([string]$Ref = 'HEAD', [string]$Output = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$commit = & git -C $root rev-parse --verify "$Ref`^{commit}"
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw 'The installer needs an existing Git commit.' }
if (-not $Output) { $Output = Join-Path $root "bug_reports\installer\EditorKrab-Setup-$($commit.Substring(0, 8)).exe" }
$Output = [IO.Path]::GetFullPath($Output)
$compiler = Join-Path ([Environment]::GetFolderPath('Windows')) 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The .NET Framework C# compiler is missing. Build on 64-bit Windows with .NET Framework 4.' }
$null = New-Item -ItemType Directory -Force -Path (Split-Path $Output -Parent)
$source = [IO.Path]::Combine([IO.Path]::GetTempPath(), 'EditorKrab-build-' + [Guid]::NewGuid() + '.cs')
try {
    [IO.File]::WriteAllText($source, ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'windows-installer.cs'))).Replace('@COMMIT@', $commit))
    & $compiler /nologo /target:winexe /reference:System.Windows.Forms.dll "/win32icon:$root\app\static\editorkrab-crab.ico" "/resource:$PSScriptRoot\install-windows.ps1,install.ps1" "/out:$Output" $source
    if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed (exit $LASTEXITCODE)." }
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Output)
    try { Write-Host ("SHA256: " + [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLower()) }
    finally { $stream.Dispose(); $sha.Dispose() }
    Write-Host "Installer: $Output"
    Write-Host "Pinned source commit: $commit"
} finally { Remove-Item -LiteralPath $source -Force }
