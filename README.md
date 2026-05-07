# Constellation

Make a private 3D map of your photos.

## One-command install

macOS Apple Silicon:

```bash
/bin/bash -c "$(curl -fsSL https://github.com/constellation/constellation/releases/latest/download/install.sh)"
```

Windows x64 PowerShell:

```powershell
irm https://github.com/constellation/constellation/releases/latest/download/install.ps1 | iex
```

Installer downloads only user-local dependencies: `uv`, Python 3.13 managed by
`uv`, Constellation release files, Python runtime wheels, and the local ONNX
image model when not bundled. No Homebrew, Python, Node, pnpm, git, Xcode, or
admin password required for the consumer path.

## Development

```bash
pnpm install
pnpm build
pnpm studio:sync
pnpm studio:app
```

### Installer test loop

```bash
pnpm installer:reset -- --yes
CONSTELLATION_RELEASE_URL="file://$PWD/dist-release/constellation-macos-arm64.tar.gz" ./scripts/install.sh
```

`installer:reset` deletes local install/app data and rebuilds `dist-release/`.
Use `--no-bundle` to skip rebuilding.

Open local page. Choose photo folder. Wait. Fly.

Photos stay on your computer.
