# @image-garden/playview

Image Garden's browser application shell.

Playview is intentionally product-specific. It is the layer that connects Studio's local API to `@image-garden/viewer` with Image Garden defaults, then adds the user-facing app experience around it.

## Responsibilities

Playview owns:

- onboarding and empty-state UI;
- folder/studio import controls;
- progress and status display;
- menu actions;
- layout tuning controls;
- wind ambience and interaction tutorial;
- debug snapshots for benchmarks;
- paginated loading from Studio's runtime asset API;
- explicit Viewer acceleration URLs for atlas and texture-array pages.

Playview does not own:

- image sanitization;
- embedding generation;
- embedding/layout persistence;
- SQLite asset indexing;
- generic rendering APIs for other projects.

Those belong to Studio and Viewer respectively.

## Development

```bash
pnpm --filter @image-garden/playview dev
pnpm --filter @image-garden/playview typecheck
pnpm --filter @image-garden/playview build
```

In production, Studio serves the built Playview files as configured static assets. Playview 0.1.x requires `@image-garden/viewer` ^0.1.0 and Image Garden Studio API 0.1, exposed by `image-garden-studio` 0.1.x.

Set `VITE_HOSTED_PRODUCTION=true` for backend-free hosted builds. It removes local/backend menu actions such as reimport, open data, clear data, and debug, while keeping camera controls, ambience, and credits visible.

In development, Playview expects the same local API shape exposed by Studio:

- `GET /api/status` with `studioApiVersion: "0.1"`
- `GET /api/assets?limit=...&offset=...`
- `POST /api/import/folder`
- `POST /api/import/studio`
- `GET /api/texture-array/index.json?...`
- `GET /api/atlas/index.json`

## Boundary

If you want to embed a flyable image map in another project, use `@image-garden/viewer` directly. If you want to generate local Image Garden data, use Studio. Use Playview when you want the Image Garden app experience that composes both.
