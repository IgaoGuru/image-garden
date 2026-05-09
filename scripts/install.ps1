$ErrorActionPreference = "Stop"
$InstallerArgs = @($args)

$AppName = "Image Garden"
$InstallDir = if ($env:IMAGE_GARDEN_INSTALL_DIR) { $env:IMAGE_GARDEN_INSTALL_DIR } elseif ($env:CONSTELLATION_INSTALL_DIR) { $env:CONSTELLATION_INSTALL_DIR } else { Join-Path $HOME ".image-garden" }
$ReleaseUrl = if ($env:IMAGE_GARDEN_RELEASE_URL) { $env:IMAGE_GARDEN_RELEASE_URL } else { $env:CONSTELLATION_RELEASE_URL }
$ReleaseSha256 = if ($env:IMAGE_GARDEN_RELEASE_SHA256) { $env:IMAGE_GARDEN_RELEASE_SHA256 } else { $env:CONSTELLATION_RELEASE_SHA256 }
$ReleaseBaseUrl = if ($env:IMAGE_GARDEN_RELEASE_BASE_URL) { $env:IMAGE_GARDEN_RELEASE_BASE_URL } elseif ($env:CONSTELLATION_RELEASE_BASE_URL) { $env:CONSTELLATION_RELEASE_BASE_URL } else { "https://github.com/IgaoGuru/image-garden/releases/latest/download" }
$UvInstallUrl = if ($env:UV_INSTALL_URL) { $env:UV_INSTALL_URL } else { "https://astral.sh/uv/install.ps1" }
$AllowInsecureChecksum = $env:IMAGE_GARDEN_ALLOW_INSECURE_CHECKSUM -eq "1" -or $env:CONSTELLATION_ALLOW_INSECURE_CHECKSUM -eq "1"
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
        { $_ -in @("AMD64", "X64") } { return "image-garden-windows-x64.zip" }
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
        if ($script:ReleaseUrl -like "file://*") {
            Write-Info "• local release checksum not provided; skipping verification"
            return
        }
        if ($script:AllowInsecureChecksum) {
            Write-Info "• checksum not provided; skipping verification by override"
            return
        }
        throw "checksum not found. Public installs require $($script:ReleaseUrl).sha256 or IMAGE_GARDEN_RELEASE_SHA256."
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

function Get-ReleaseVersion($Root) {
    $versionFile = Join-Path $Root "VERSION"
    if (Test-Path $versionFile) {
        $value = (Get-Content -Path $versionFile -TotalCount 1).Trim()
        $clean = -join ($value.ToCharArray() | Where-Object { [char]::IsLetterOrDigit($_) -or $_ -in @('.', '_', '-') })
        if ($clean) { return $clean }
    }
    return (((Split-Path -Leaf $Root) -replace '^image-garden-', '') -replace '^constellation-', '')
}

function Install-ExtractedRelease($Root) {
    $parent = Split-Path -Parent $InstallDir
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $version = Get-ReleaseVersion $Root
    if (-not $version) { $version = "dev" }
    $releases = Join-Path $InstallDir "releases"
    $releaseDir = Join-Path $releases $version
    $current = Join-Path $InstallDir "current"
    $backup = Join-Path $script:TempDir "previous-install"
    $oldReleaseBackup = Join-Path $script:TempDir "previous-release"

    if ((Test-Path $InstallDir) -and -not (Test-Path $releases) -and -not (Test-Path $current)) {
        Move-Item -Force -LiteralPath $InstallDir -Destination $backup
    }
    New-Item -ItemType Directory -Force -Path $releases | Out-Null
    if (Test-Path $releaseDir) {
        Move-Item -Force -LiteralPath $releaseDir -Destination $oldReleaseBackup
    }
    try {
        Move-Item -Force -LiteralPath $Root -Destination $releaseDir
        if (Test-Path $current) {
            Remove-Item -Recurse -Force -LiteralPath $current
        }
        try {
            New-Item -ItemType Junction -Path $current -Target $releaseDir | Out-Null
        }
        catch {
            New-Item -ItemType SymbolicLink -Path $current -Target $releaseDir | Out-Null
        }
    }
    catch {
        if (Test-Path $oldReleaseBackup) {
            Move-Item -Force -LiteralPath $oldReleaseBackup -Destination $releaseDir
        }
        if (Test-Path $backup) {
            if (Test-Path $InstallDir) { Remove-Item -Recurse -Force -LiteralPath $InstallDir }
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
    $appDir = Join-Path $InstallDir "current"
    $tui = Join-Path $appDir "scripts\install_tui.py"
    if (-not (Test-Path $tui)) {
        throw "installer TUI missing: $tui"
    }
    Write-Info "Starting installer UI..."
    uv run --no-project --python 3.13 $tui --app-dir $appDir @InstallerArgs
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
