# Image Garden

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

The installer creates a user-local `image-garden` command. After install:

```bash
image-garden start    # start the local app here; Ctrl+C stops it
image-garden open     # reopen the browser while it is running
image-garden start --background # advanced: return to the shell
image-garden stop     # stop a background run
image-garden status   # show URL/PID/version/model state
image-garden logs     # inspect logs
image-garden doctor   # diagnose install issues
image-garden update   # update app files
image-garden rollback # return to previous release
image-garden uninstall
```

No `.app` bundle is required. The app is CLI-managed and opens in your browser.

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

### Release

To build local release assets only:

```bash
pnpm release:bundle
```

That builds web assets, locks/stages Studio, writes platform archives,
checksums, bootstrap installers, and `dist-release/release-manifest.json`.

To publish an official GitHub release after committing/merging:

```bash
./release-tag 0.2.1
```

That validates, builds `dist-release/` as `v0.2.1`, creates/pushes the git tag,
and uploads release assets with `gh`.

### Installer test loop

```bash
pnpm installer:reset -- --yes
IMAGE_GARDEN_RELEASE_URL="file://$PWD/dist-release/image-garden-macos-arm64.tar.gz" ./scripts/install.sh --recommended --no-launch
~/.local/bin/image-garden start --background --no-open
~/.local/bin/image-garden status
~/.local/bin/image-garden stop
```

`installer:reset` deletes local install/app data and rebuilds `dist-release/`.
Use `--no-bundle` to skip rebuilding.

Open local page. Choose photo folder. Wait. Fly.

Photos stay on your computer.
