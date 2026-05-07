$ErrorActionPreference = "Stop"
$InstallerArgs = @($args)

$AppName = "Constellation"
$InstallDir = if ($env:CONSTELLATION_INSTALL_DIR) { $env:CONSTELLATION_INSTALL_DIR } else { Join-Path $HOME ".constellation" }
$ReleaseUrl = $env:CONSTELLATION_RELEASE_URL
$ReleaseSha256 = $env:CONSTELLATION_RELEASE_SHA256
$ReleaseBaseUrl = if ($env:CONSTELLATION_RELEASE_BASE_URL) { $env:CONSTELLATION_RELEASE_BASE_URL } else { "https://github.com/IgaoGuru/image-garden/releases/latest/download" }
$UvInstallUrl = if ($env:UV_INSTALL_URL) { $env:UV_INSTALL_URL } else { "https://astral.sh/uv/install.ps1" }
$TempDir = $null

function Write-Info($Message) {
    Write-Host $Message
}

function Write-Err($Message) {
    Write-Host "error: $Message" -ForegroundColor Red
}

function Test-Command($Name) {
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Show-Header {
    Clear-Host
    Write-Info "✦ $AppName installer"
    Write-Info "Install folder: $InstallDir"
    Write-Info ""
}

function Get-WindowsArchitecture {
    $envArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    if ($envArch) {
        return $envArch.ToString().ToUpperInvariant()
    }
    try {
        return ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture).ToString().ToUpperInvariant()
    }
    catch {
        return "UNKNOWN"
    }
}

function Get-AssetName {
    $arch = Get-WindowsArchitecture
    switch ($arch) {
        { $_ -in @("AMD64", "X64") } { return "constellation-windows-x64.zip" }
        default { throw "unsupported Windows architecture: $arch. Windows x64/AMD64 is supported for now." }
    }
}

function Install-Uv {
    if (Test-Command "uv") {
        Write-Info "✓ uv found: $((Get-Command uv).Source)"
        return
    }

    $candidateDirs = @(
        (Join-Path $HOME ".local\bin"),
        (Join-Path $HOME ".cargo\bin")
    )
    foreach ($dir in $candidateDirs) {
        $env:Path = "$dir;$env:Path"
    }
    if (Test-Command "uv") {
        Write-Info "✓ uv found: $((Get-Command uv).Source)"
        return
    }

    Write-Info "Installing uv runtime manager..."
    Invoke-RestMethod $UvInstallUrl | Invoke-Expression
    foreach ($dir in $candidateDirs) {
        $env:Path = "$dir;$env:Path"
    }
    if (-not (Test-Command "uv")) {
        throw "uv install finished, but uv is not on PATH. Restart PowerShell and rerun installer."
    }
    Write-Info "✓ uv installed: $((Get-Command uv).Source)"
}

function Load-RemoteSha256($ChecksumFile) {
    if ($script:ReleaseSha256) {
        return
    }
    try {
        Invoke-WebRequest -Uri "$($script:ReleaseUrl).sha256" -OutFile $ChecksumFile
        $script:ReleaseSha256 = ((Get-Content -Path $ChecksumFile -TotalCount 1) -split "\s+")[0]
        Write-Info "✓ checksum file found"
    }
    catch {
        # Optional for local/dev releases. Production releases should publish .sha256.
    }
}

function Verify-Sha256($File, $Expected) {
    if (-not $Expected) {
        Write-Info "• checksum not provided; skipping verification"
        return
    }
    $actual = (Get-FileHash -Algorithm SHA256 -Path $File).Hash.ToLowerInvariant()
    $expectedLower = $Expected.ToLowerInvariant()
    if ($actual -ne $expectedLower) {
        throw "checksum mismatch. expected $expectedLower, got $actual"
    }
    Write-Info "✓ checksum verified"
}

function Test-ExtractedRelease($Root) {
    foreach ($required in @("studio", "viewer-dist", "playview-dist", "scripts\install_tui.py")) {
        if (-not (Test-Path (Join-Path $Root $required))) {
            throw "release missing $required"
        }
    }
}

function Install-ExtractedRelease($Root) {
    $parent = Split-Path -Parent $InstallDir
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $backup = Join-Path $script:TempDir "previous-install"
    if (Test-Path $InstallDir) {
        Move-Item -Force -LiteralPath $InstallDir -Destination $backup
    }
    try {
        Move-Item -Force -LiteralPath $Root -Destination $InstallDir
    }
    catch {
        if (Test-Path $backup) {
            Move-Item -Force -LiteralPath $backup -Destination $InstallDir
        }
        throw
    }
}

function Download-Release {
    $assetName = Get-AssetName
    if (-not $script:ReleaseUrl) {
        $script:ReleaseUrl = "$ReleaseBaseUrl/$assetName"
    }
    $script:TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Force -Path $script:TempDir | Out-Null
    $archive = Join-Path $script:TempDir $assetName

    Write-Info "Downloading $AppName release..."
    Write-Info $script:ReleaseUrl
    Invoke-WebRequest -Uri $script:ReleaseUrl -OutFile $archive
    Load-RemoteSha256 (Join-Path $script:TempDir "$assetName.sha256")
    Verify-Sha256 $archive $script:ReleaseSha256

    Write-Info "Installing app files..."
    $extractDir = Join-Path $script:TempDir "extract"
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
    Expand-Archive -Path $archive -DestinationPath $extractDir -Force
    $root = Get-ChildItem -Directory -Path $extractDir | Select-Object -First 1
    if (-not $root) {
        throw "release archive did not contain an app directory"
    }
    Test-ExtractedRelease $root.FullName
    Install-ExtractedRelease $root.FullName
}

function Start-InstallerTui {
    $tui = Join-Path $InstallDir "scripts\install_tui.py"
    if (-not (Test-Path $tui)) {
        throw "installer TUI missing: $tui"
    }
    Write-Info "Starting installer UI..."
    uv run --no-project --python 3.13 $tui --app-dir $InstallDir @InstallerArgs
}

try {
    Show-Header
    Install-Uv
    Download-Release
    Start-InstallerTui
}
finally {
    if ($TempDir -and (Test-Path $TempDir)) {
        Remove-Item -Recurse -Force $TempDir
    }
}
