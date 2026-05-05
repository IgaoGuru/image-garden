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
pnpm studio:embed /path/to/photos --output constellation.json --skip-errors
pnpm build
pnpm studio:serve
```

The `studio:*` scripts load `.env` automatically before invoking `uv`.

## Checks

```bash
pnpm test
pnpm test:e2e
pnpm studio:test
```
