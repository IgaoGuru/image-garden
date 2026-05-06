$ErrorActionPreference = "Stop"

$InstallDir = if ($env:CONSTELLATION_INSTALL_DIR) { $env:CONSTELLATION_INSTALL_DIR } else { Join-Path $HOME ".constellation" }
$ReleaseUrl = $env:CONSTELLATION_RELEASE_URL
$GitRepo = if ($env:CONSTELLATION_GIT_REPO) { $env:CONSTELLATION_GIT_REPO } else { "https://github.com/constellation/constellation.git" }

Write-Host "✦ Constellation installer"
Write-Host "Install directory: $InstallDir"

function Test-Command($Name) {
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-Command "uv")) {
    Write-Host "Installing uv..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$HOME\.local\bin;$HOME\.cargo\bin;$env:Path"
    if (-not (Test-Command "uv")) {
        throw "uv installation did not put uv on PATH. Restart PowerShell and rerun this installer."
    }
}
Write-Host "✓ uv ready"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if ($ReleaseUrl) {
    $Tmp = New-Item -ItemType Directory -Force -Path (Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString()))
    $Archive = Join-Path $Tmp "constellation.zip"
    Write-Host "Downloading Constellation release..."
    Invoke-WebRequest -Uri $ReleaseUrl -OutFile $Archive
    Expand-Archive -Path $Archive -DestinationPath $InstallDir -Force
    Remove-Item -Recurse -Force $Tmp
} elseif (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Host "Updating existing checkout..."
    git -C $InstallDir pull --ff-only
} else {
    if (-not (Test-Command "git")) {
        throw "git is required when CONSTELLATION_RELEASE_URL is not set. Use a release installer URL or install git and rerun."
    }
    Write-Host "Cloning Constellation..."
    git clone $GitRepo $InstallDir
}

Set-Location $InstallDir
Write-Host "Preparing local Python environment..."
uv --directory studio sync --inexact --extra onnx

if ((Test-Path "package.json") -and (Test-Command "pnpm")) {
    Write-Host "Building viewer assets..."
    pnpm install --frozen-lockfile
    pnpm --filter '@constellation/viewer' build
} else {
    Write-Host "Viewer build skipped; using bundled viewer-dist if present."
}

Write-Host "Starting Constellation..."
uv --project studio run constellation-app @args
