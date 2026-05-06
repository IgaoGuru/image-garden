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

## Local backend and desktop shell

The desktop-ready prototype keeps folder import in Studio while persisting positioned runtime assets in SQLite:

```bash
pnpm studio:backend -- --data-dir .constellation-backend
curl -X POST http://127.0.0.1:8766/api/import/folder \
  -H 'Content-Type: application/json' \
  -d '{"path":"/path/to/photos"}'
```

The local API includes `/api/status`, `/api/assets`, `/api/assets/near`, `/api/assets/:id`, `/api/thumbnails/:id`, `/api/files/:id`, and basic import/index lifecycle routes.

A thin Electron shell lives in `apps/desktop` and starts/connects to this backend without rewriting the viewer renderer:

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
