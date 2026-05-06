# Constellation Studio

Local ingestion, embedding, and preview tools for `@constellation/viewer`.

## What Studio does now

`constellation-embed` is a full local pipeline:

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
- `--url-prefix /assets/` sets the server URL prefix for generated assets.

## Serve preview

```bash
pnpm build
pnpm studio:serve
```

`embed` writes a local sidecar such as `constellation.studio.json` so `serve` can find the generated asset directory when run with no arguments.

Routes:

- `/` serves a local preview page.
- `/data.json` serves the generated Constellation JSON.
- `/assets/images/<hash>.jpg` serves canonical JPEGs by default.
- `/assets/thumbs/<hash>.jpg` serves thumbnails by default.
- `/viewer-entry.js` auto-detects and re-exports the built viewer entry when a viewer dist is available.
- `/viewer/*` serves `packages/viewer/dist` automatically when it exists, or a path passed by `--viewer-dist`.

If the viewer package is not built yet, the preview page falls back to a simple grid while still verifying that Studio's JSON and image serving work.
