# Image Garden Studio

Local CLI/backend/data engine for Image Garden.

Studio owns folder import, image sanitization, thumbnail generation, embedding/cache work, layout computation, SQLite runtime asset storage, and the local HTTP API consumed by Playview. It can serve configured static Viewer/Playview assets for development and releases, but product UI behavior belongs in Playview and generic rendering behavior belongs in `@image-garden/viewer`.

Publish target: Studio is a Python package/CLI named `image-garden-studio`. The user-facing release still exposes the bundled `image-garden` command, while lower-level scripts such as `image-garden-backend`, `image-garden-embed`, and `image-garden-download-onnx` are available for development and automation.

## What Studio does now

`image-garden-embed` is a full local pipeline:

1. Recursively discovers still images, including `.heic` / `.heif` when `pillow-heif` can decode them.
2. Converts every source image into backend-owned, browser-friendly JPEG assets.
3. Hashes the canonical JPEG bytes and uses that SHA-256 hash as the image id.
4. Writes:
   - `images/<hash>.jpg` canonical display JPEGs
   - `thumbs/<hash>.jpg` lightweight viewer texture JPEGs
   - `cache/embeddings/.../<hash>.json` cached CLIP embeddings
5. Emits `constellation.json` with `url`, `thumbnailUrl`, dimensions, metadata, and embeddings.

Original filenames are not used as ids. Videos and Live Photo video sidecars are ignored.

## Setup

From the repo root, the preferred scripts load `.env` automatically:

```bash
pnpm studio:sync
```

## Embed images

```bash
# from the repo root
pnpm studio:embed /path/to/photos --output constellation.json --batch-size 8 --skip-errors
```

Default output layout for `--output constellation.json`:

```text
constellation.json
constellation.studio.json
constellation-assets/
├── images/
│   └── <sha256>.jpg
├── thumbs/
│   └── <sha256>.jpg
└── cache/
    └── embeddings/
        └── open_clip_.../
            └── <sha256>.json
```

The viewer JSON looks like:

```json
{
  "images": [
    {
      "id": "<sha256>",
      "url": "/assets/images/<sha256>.jpg",
      "thumbnailUrl": "/assets/thumbs/<sha256>.jpg",
      "width": 1600,
      "height": 1200,
      "embedding": [0.1],
      "metadata": {
        "sourcePath": "/original/source/path.heic"
      }
    }
  ]
}
```

Useful options:

- `--asset-dir DIR` chooses where sanitized JPEGs, thumbnails, and cache live.
- `--max-image-size 2048` controls canonical JPEG long edge.
- `--thumbnail-size 384` controls thumbnail long edge.
- `--jpeg-quality 90` controls generated JPEG quality.
- `--device auto|mps|cuda|cpu` chooses the torch device.
- `--batch-size 8` controls CLIP inference batch size.
- `--skip-errors` skips unreadable images and failed embeddings.
- `--no-cache` recomputes embeddings instead of using the embedding cache.
- `--model` and `--pretrained` are passed to `open_clip.create_model_and_transforms`.
- `--embedding-engine onnx --onnx-model models/mobileclip-s1-vision.onnx` uses the local MobileCLIP-S1 ONNX path.
- `--url-prefix /assets/` sets the server URL prefix for generated assets.

## ONNX model downloads

The browser-first app defaults to MobileCLIP-S1 ONNX from `Xenova/mobileclip_s1`.
The downloader writes both `mobileclip-s1-vision.onnx` and the companion
`preprocessor_config.json` so preprocessing matches the model.

```bash
pnpm studio:download-onnx
```

Legacy CLIP ViT-B/32 is still supported:

```bash
pnpm studio:download-onnx -- --model clip-vit-base-patch32
```

Models are not bundled in releases; installer/app setup downloads them into
user-local app data. Embeddings stay local. The `onnx` optional dependency installs ONNX Runtime for the default local engine; the `openclip` optional dependency installs the advanced PyTorch/OpenCLIP path for experimentation.

## Bring your own dataset

Studio supports a single portable dataset contract for generated and external
assets: `constellation.json` with a top-level `images` array. BYO datasets may
reference existing JPEGs/thumbnails in place; Studio preserves caller-provided
ids and never regenerates assets during dataset import.

```json
{
  "images": [
    {
      "id": "page-0001",
      "url": "source-images/page-0001.jpg",
      "thumbnailUrl": "thumbs/page-0001.jpg",
      "width": 377,
      "height": 512,
      "metadata": { "source": "external" }
    }
  ]
}
```

Supported image references are local paths relative to the dataset JSON,
`file://` URLs, absolute local paths, or URL-style paths under the sidecar
`urlPrefix`. HTTP(S) URLs are intentionally rejected by the local importer.

Layout inputs are resolved in this order:

1. If every image has `position`, Studio imports those runtime positions
   directly.
2. Pass `--recompute-layout` to ignore existing positions and rebuild layout
   from embeddings/the configured embedding engine.
3. Otherwise, Studio uses every provided `embedding` and computes any missing
   embeddings from `url`/`fullUrl` with the configured embedding engine, then
   builds the 3D layout.
4. If positions are incomplete and embeddings are missing while no embedding
   engine is configured, import fails with a clear error.

Optional sidecar `constellation.studio.json` remains supported:

```json
{
  "imageRoot": "/absolute/path/to/assets",
  "dataJson": "/absolute/path/to/constellation.json",
  "urlPrefix": "/assets/"
}
```

Import from the CLI to build the runtime SQLite index, computing missing
embeddings when an embedding engine is configured:

```bash
image-garden-import-dataset /path/to/constellation.json \
  --data-dir .image-garden-backend \
  --embedding-engine onnx \
  --onnx-model /path/to/mobileclip-s1-vision.onnx
```

Export runtime positions back into the original portable JSON while
preserving unrelated fields:

```bash
image-garden-export-positions /path/to/constellation.json \
  --data-dir .image-garden-backend
```

Import via backend API:

```bash
curl -X POST http://127.0.0.1:8766/api/import/studio \
  -H 'Content-Type: application/json' \
  -d '{"path":"/path/to/constellation.json", "assetDir":"/path/to/assets"}'
```

## Local backend API prototype

`image-garden-backend` persists positioned runtime assets in SQLite while continuing to reuse the folder JPEG sanitization path:

```bash
pnpm studio:backend -- --data-dir .image-garden-backend
curl -X POST http://127.0.0.1:8766/api/import/folder \
  -H 'Content-Type: application/json' \
  -d '{"path":"/path/to/photos"}'
```

API routes:

- `GET /api/status`
- `GET /api/assets?limit=...&offset=...`
- `GET /api/assets/near?x=...&y=...&z=...&radius=...`
- `GET /api/assets/:id`
- `GET /api/thumbnails/:id`
- `GET /api/files/:id`
- `POST /api/import/folder`
- `POST /api/index/start`
- `POST /api/index/pause`
- `POST /api/index/resume`

Runtime asset responses contain `id`, `thumbnailUrl`, optional `fullUrl`, size/metadata, and a persisted `position`; embeddings remain indexer/backend data and are not emitted on this runtime API. This API is Studio's contract with Playview and with the Viewer `createStudioDataSource()` adapter.

## Serve preview

```bash
pnpm build
pnpm studio:serve
```

`embed` writes a local sidecar such as `constellation.studio.json` so `serve` can find the generated asset directory when run with no arguments.

Routes:

- `/` serves a local preview page.
- `/data.json` serves the generated Image Garden/Viewer JSON.
- `/assets/images/<hash>.jpg` serves canonical JPEGs by default.
- `/assets/thumbs/<hash>.jpg` serves thumbnails by default.
- `/viewer-entry.js` auto-detects and re-exports the built viewer entry when a viewer dist is available.
- `/viewer/*` serves `packages/viewer/dist` automatically when it exists, or a path passed by `--viewer-dist`.

If the viewer package is not built yet, the preview page falls back to a simple grid while still verifying that Studio's JSON and image serving work.
