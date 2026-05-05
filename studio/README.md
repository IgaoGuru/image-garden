# Constellation Studio

Local tools for generating and previewing the JSON consumed by `@constellation/viewer`.

## Setup

Studio is designed to run with the existing ML uv environment:

```bash
# from the repo root
cd studio
UV_PROJECT_ENVIRONMENT=$HOME/.venvs/ml uv sync --inexact
cd ..
```

## Embed images

```bash
# from the repo root
python studio/embed.py ./photos --output constellation.json

# or, from ./studio after uv sync:
cd studio
UV_PROJECT_ENVIRONMENT=$HOME/.venvs/ml uv run constellation-embed ../photos --output ../constellation.json
```

The output follows the shared contract:

```json
{
  "images": [
    { "id": "photo.jpg", "url": "/images/photo.jpg", "embedding": [0.1] }
  ]
}
```

Useful options:

- `--device auto|mps|cuda|cpu` chooses the torch device (`auto` prefers CUDA, then MPS, then CPU).
- `--batch-size 32` controls CLIP inference batch size.
- `--skip-errors` retries failed batches one-by-one and skips unreadable images.
- `--model` and `--pretrained` are passed to `open_clip.create_model_and_transforms`.
- `--url-prefix /images/` sets the URL prefix used in JSON.

## Serve preview

```bash
# from the repo root after embed.py wrote constellation.json + constellation.studio.json
python studio/serve.py

# or pass paths explicitly
python studio/serve.py ./photos constellation.json

# add --no-open to avoid opening a browser
```

`embed.py` writes a local sidecar such as `constellation.studio.json` so `serve.py` can find the image directory when run with no arguments.

Routes:

- `/` serves a local preview page.
- `/data.json` serves the generated Constellation JSON.
- `/images/<relative-path>` serves original images from the image directory by default; if `embed.py --url-prefix` used a different prefix, `serve.py` uses that manifest value or an explicit `serve.py --url-prefix` override.
- `/viewer-entry.js` auto-detects and re-exports the built viewer entry when a viewer dist is available.
- `/viewer/*` serves `packages/viewer/dist` automatically when it exists, or a path passed by `--viewer-dist`.

If the viewer package is not built yet, the preview page falls back to a simple grid while still verifying that Studio's JSON and image serving work.
