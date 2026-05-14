# Image Garden

Image Garden is a software toy for exploring your photo library as a 3D embedding map.

<video src="https://file.garden/aDx7rtkwXCXfZGMO/imagegarden/image-garden-3-final-compressed.mp4" controls muted loop playsinline width="100%">
  <a href="https://file.garden/aDx7rtkwXCXfZGMO/imagegarden/image-garden-3-final-compressed.mp4">Watch the Image Garden demo video.</a>
</video>

**Website:** https://igaoguru.github.io/image-garden/

## What it does

- Builds a local embedding map of your images.
- Projects image embeddings into a navigable 3D world.
- Generates thumbnails and caches embeddings locally.
- Uses WebGL instancing, texture pages, culling, and LOD so large maps stay usable.
- Opens as a local web app; no desktop app shell is required.

Images stay on your computer. No cloud photo upload is required.

## Install

### macOS Apple Silicon

Open **Terminal** and run:

```bash
/bin/bash -c "$(curl -fsSL https://github.com/IgaoGuru/image-garden/releases/latest/download/install.sh)"
```

### Windows x64

Open **PowerShell** and run:

```powershell
irm https://github.com/IgaoGuru/image-garden/releases/latest/download/install.ps1 | iex
```

The installer creates a user-local `image-garden` command:

```bash
image-garden start    # start the local app; Ctrl+C stops it
image-garden open     # reopen the browser while it is running
image-garden stop     # stop a background run
image-garden status   # show URL/PID/version/model state
image-garden logs     # inspect logs
image-garden doctor   # diagnose install issues
image-garden update   # update app files
image-garden rollback # return to previous release
image-garden uninstall
```

Installer setup is user-local: `uv`, Python 3.13 managed by `uv`, Image Garden release files, Python runtime wheels, and the MobileCLIP-S1 ONNX image model. No Homebrew, Python, Node, pnpm, git, Xcode, or admin password is required for normal installation.

## Packages

Image Garden is split into three publishable pieces:

- `@image-garden/viewer` (`packages/viewer`): reusable Three/WebGL renderer for positioned image maps.
- `@image-garden/playview` (`packages/playview`): Image Garden's browser app shell, onboarding, import UI, menu, audio, tuning controls, and Viewer wiring.
- `image-garden-studio` (`packages/studio`): Python CLI/backend for folder import, image sanitization, embeddings, layout, SQLite state, generated assets, and the local API.

Playview composes Studio and Viewer. Studio exposes the local API and static release files; Viewer remains reusable without Studio when given direct/static/custom data sources.

## Development

```bash
pnpm install
pnpm build
pnpm studio:sync
pnpm studio:app
```

Useful commands:

```bash
pnpm playview:dev
pnpm playview:typecheck
pnpm --filter @image-garden/viewer test:e2e
uv --project packages/studio run python -m pytest -q packages/studio/tests
```

`pnpm studio:download-onnx` downloads the default MobileCLIP-S1 ONNX image encoder from `Xenova/mobileclip_s1`. Legacy CLIP ViT-B/32 remains available with:

```bash
pnpm studio:download-onnx -- --model clip-vit-base-patch32
```

## Release

Build local release assets:

```bash
pnpm release:bundle
```

Publish an official GitHub release after committing/merging:

```bash
./scripts/release-tag.sh 0.2.1
```

Installer test loop:

```bash
pnpm installer:reset -- --yes
IMAGE_GARDEN_RELEASE_URL="file://$PWD/dist-release/image-garden-macos-arm64.tar.gz" ./scripts/install.sh --recommended --no-launch
~/.local/bin/image-garden start --background --no-open
~/.local/bin/image-garden status
~/.local/bin/image-garden stop
```
