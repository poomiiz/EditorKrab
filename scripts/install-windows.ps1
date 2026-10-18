# Online bootstrap embedded in the Windows installer. No personal files are bundled.
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$Commit,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\EditorKrab'),
    [string]$Workspace = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'EditorKrab Workspace')
)
$ErrorActionPreference = 'Stop'
$repository = 'https://github.com/poomiiz/EditorKrab.git'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)

function Invoke-Git([string[]]$Arguments) {
    $result = & git @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Git failed (exit $LASTEXITCODE): $($Arguments -join ' ')" }
    return $result
}

if (Test-Path -LiteralPath $InstallRoot) {
    if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot '.git'))) {
        throw "Folder already exists and is not an EditorKrab install: $InstallRoot. Choose an empty install location."
    }
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'Git and winget are missing. Install App Installer from Microsoft Store or install Git, then retry.'
    }
    & winget install --id Git.Git -e --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Installing Git failed (exit $LASTEXITCODE)." }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git is not on PATH yet. Run the installer again after signing back into Windows.' }
}
if (Test-Path -LiteralPath $InstallRoot) {
    $origin = Invoke-Git -Arguments @('-C', $InstallRoot, 'remote', 'get-url', 'origin')
    $installed = Invoke-Git -Arguments @('-C', $InstallRoot, 'rev-parse', 'HEAD')
    if ($origin -ne $repository -or $installed -ne $Commit) {
        throw 'A different install/version already exists. Use Update inside EditorKrab, or choose another install location.'
    }
    if (Invoke-Git -Arguments @('-C', $InstallRoot, 'status', '--porcelain', '--untracked-files=no')) {
        throw 'This install has local code edits. Keep them and use Update inside EditorKrab before running setup again.'
    }
} else {
    $staging = $InstallRoot + '.download-' + [Guid]::NewGuid()
    Invoke-Git -Arguments @('clone', '--no-checkout', $repository, $staging)
    Invoke-Git -Arguments @('-C', $staging, 'cat-file', '-e', "$Commit`^{commit}")
    Invoke-Git -Arguments @('-C', $staging, 'checkout', '--detach', $Commit)
    # Same-parent staging is ours; never merge files into a folder another process created.
    if (Test-Path -LiteralPath $InstallRoot) { throw "Install location was created during download: $InstallRoot" }
    if ([IO.Path]::GetDirectoryName($staging) -ne [IO.Path]::GetDirectoryName($InstallRoot)) { throw 'Invalid staging location.' }
    [IO.Directory]::Move($staging, $InstallRoot)
}

# Re-running setup must retain the workspace and paths already chosen by this user.
$existingConfig = Test-Path -LiteralPath (Join-Path $InstallRoot 'config.json')
$setupArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $InstallRoot 'scripts\setup.ps1'))
if (-not $existingConfig) { $setupArgs += @('-Workspace', $Workspace) }
& powershell.exe @setupArgs
if ($LASTEXITCODE -ne 0) { throw "EditorKrab setup failed (exit $LASTEXITCODE). Fix the reported check, then run the installer again." }
Write-Host "EditorKrab is installed in $InstallRoot. Open the desktop shortcut."
