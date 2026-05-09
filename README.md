# Constellation

Make a private 3D map of your photos.

## Install

### macOS Apple Silicon

1. Open **Terminal**.
2. Paste and run:

```bash
/bin/bash -c "$(curl -fsSL https://github.com/IgaoGuru/image-garden/releases/latest/download/install.sh)"
```

### Windows x64

1. Open **PowerShell**.
2. Paste and run:

```powershell
irm https://github.com/IgaoGuru/image-garden/releases/latest/download/install.ps1 | iex
```

Then use the arrow keys in the installer and press **Enter** on the recommended
option.

Installer downloads only user-local dependencies: `uv`, Python 3.13 managed by
`uv`, Image Garden release files, Python runtime wheels, and the local
MobileCLIP-S1 ONNX image model. The app download is small; the model downloads
separately during setup. No Homebrew, Python, Node, pnpm, git, Xcode, or admin
password required.

## Development

```bash
pnpm install
pnpm build
pnpm studio:sync
pnpm studio:app
```

`pnpm studio:download-onnx` downloads the default MobileCLIP-S1 ONNX image
encoder from `Xenova/mobileclip_s1`. Legacy CLIP ViT-B/32 remains available with
`pnpm studio:download-onnx -- --model clip-vit-base-patch32`.

### Installer test loop

```bash
pnpm installer:reset -- --yes
CONSTELLATION_RELEASE_URL="file://$PWD/dist-release/constellation-macos-arm64.tar.gz" ./scripts/install.sh
```

`installer:reset` deletes local install/app data and rebuilds `dist-release/`.
Use `--no-bundle` to skip rebuilding.

Open local page. Choose photo folder. Wait. Fly.

Photos stay on your computer.
