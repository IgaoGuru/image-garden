param(
    [switch]$Yes,
    [switch]$KeepData,
    [switch]$NoBundle,
    [string]$InstallDir = $(if ($env:CONSTELLATION_INSTALL_DIR) { $env:CONSTELLATION_INSTALL_DIR } else { Join-Path $HOME ".constellation" }),
    [string]$DataDir = $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "Constellation" } else { Join-Path $HOME "AppData\Local\Constellation" })
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$BundleDir = if ($env:CONSTELLATION_RELEASE_DIR) { $env:CONSTELLATION_RELEASE_DIR } else { Join-Path $Root "dist-release" }

Write-Host "✦ Reset Constellation install state"
Write-Host ""
Write-Host "Will delete:"
Write-Host "  install dir: $InstallDir"
if ($KeepData) {
    Write-Host "  app data:    kept"
} else {
    Write-Host "  app data:    $DataDir"
}
Write-Host ""
Write-Host "Will keep:"
Write-Host "  uv binary/cache"
Write-Host "  system Python/package managers"
Write-Host "  photo library"
Write-Host "  repo files"
if (-not $NoBundle) {
    Write-Host ""
    Write-Host "Will rebuild:"
    Write-Host "  $BundleDir via pnpm release:bundle"
}
Write-Host ""

$running = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessName -match "constellation|python|uv" -and $_.Path -like "*$InstallDir*"
}
if ($running) {
    Write-Host "warning: Constellation may still be running. Stop it before reset if delete fails." -ForegroundColor Yellow
    Write-Host ""
}

if (-not $Yes) {
    $answer = Read-Host "Delete these paths? [y/N]"
    if ($answer -notin @("y", "Y", "yes", "YES")) {
        Write-Host "Aborted."
        exit 0
    }
}

function Remove-PathIfExists($Path) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -Recurse -Force -LiteralPath $Path
        Write-Host "✓ removed $Path"
    } else {
        Write-Host "• not found $Path"
    }
}

Remove-PathIfExists $InstallDir
if (-not $KeepData) {
    Remove-PathIfExists $DataDir
}

if (-not $NoBundle) {
    Write-Host ""
    Write-Host "Rebuilding local release bundle..."
    Push-Location $Root
    try {
        pnpm release:bundle
    }
    finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "Reset done. Run installer again."
if (-not $NoBundle) {
    Write-Host "Local Windows installer command:"
    Write-Host "  `$env:CONSTELLATION_RELEASE_URL = 'file:///$($BundleDir -replace '\\', '/')/constellation-windows-x64.zip'; ./scripts/install.ps1"
}
