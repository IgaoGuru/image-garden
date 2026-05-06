# Constellation

Image embedding 3D explorer monorepo.

## Local environment

A local `.env` file is used by the Studio npm scripts so you do not need to prefix every command with `UV_PROJECT_ENVIRONMENT=...`.

This repo includes a local `.env` for this machine:

```dotenv
UV_PROJECT_ENVIRONMENT=/Users/igorrocha/.venvs/ml
```

For another machine, copy `.env.example` to `.env` and edit the venv path.

## Viewer demo

```bash
pnpm install
pnpm dev
```

Open the Vite URL, usually `http://127.0.0.1:5173/demo/`.

## Studio workflow

From the repo root:

```bash
pnpm studio:sync
pnpm studio:embed /path/to/photos --output constellation.json --batch-size 8 --skip-errors
pnpm build
pnpm studio:serve
```

The `studio:*` scripts load `.env` automatically before invoking `uv`.

`studio:embed` ingests HEIC/HEIF/JPEG/PNG/etc. sources into sanitized JPEG assets under `constellation-assets/`, hashes the canonical JPEG bytes for stable ids, writes thumbnails, and caches embeddings for reruns.

## Local app

The preferred local-first flow starts the Studio backend and opens the browser UI directly; Electron is not required:

```bash
pnpm --filter @constellation/viewer build
pnpm studio:app
```

By default, `constellation-app` uses a bundled ONNX model when one is present under `models/`; otherwise it downloads the default Hugging Face ONNX CLIP image model into the app data directory. To choose an engine explicitly:

```bash
pnpm studio:app -- --embedding-engine openclip
pnpm studio:app -- --embedding-engine onnx --onnx-model /path/to/clip-image-encoder.onnx
```

The consumer target is the ONNX/native engine. The OpenCLIP/PyTorch engine remains available for development and advanced users.

To download the default ONNX model manually:

```bash
pnpm studio:download-onnx
```

This writes `models/clip-image-encoder.onnx`, which `constellation-app` auto-detects.

## Release/installer scaffolding

Installer and release scaffolding lives under `scripts/`:

```bash
pnpm release:bundle
bash scripts/install.sh
pwsh scripts/install.ps1
```

`release:bundle` stages Studio, prebuilt viewer assets, and launch scripts into `dist-release/`.

## Local backend and optional desktop shell

The backend keeps folder import in Studio while persisting positioned runtime assets in SQLite:

```bash
pnpm studio:backend -- --data-dir .constellation-backend
curl -X POST http://127.0.0.1:8766/api/import/folder \
  -H 'Content-Type: application/json' \
  -d '{"path":"/path/to/photos"}'
```

The local API includes `/api/status`, `/api/sources`, `/api/assets`, `/api/assets/near`, `/api/assets/:id`, `/api/thumbnails/:id`, `/api/files/:id`, `POST /api/import/folder`, `POST /api/import/studio`, and basic import/index lifecycle routes.

Constellation is intentionally **bring-your-own photos** for now: import a local image directory/export, or open a portable Constellation Studio dataset (`constellation.json` / `constellation.studio.json`). No cloud photo connectors are exposed.

A thin Electron shell still lives in `apps/desktop` and starts/connects to this backend without rewriting the viewer renderer:

```bash
pnpm --filter @constellation/viewer build
pnpm desktop:dev
```

## Checks

```bash
pnpm test
pnpm test:e2e
pnpm studio:test
```
