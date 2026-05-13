# @constellation/playview

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
pnpm --filter @constellation/playview dev
pnpm --filter @constellation/playview typecheck
pnpm --filter @constellation/playview build
```

In production, Studio serves the built Playview files as configured static assets. In development, Playview expects the same local API shape exposed by Studio:

- `GET /api/status`
- `GET /api/assets?limit=...&offset=...`
- `POST /api/import/folder`
- `POST /api/import/studio`
- `GET /api/texture-array/index.json?...`
- `GET /api/atlas/index.json`

## Boundary

If you want to embed a flyable image map in another project, use `@image-garden/viewer` directly. If you want to generate local Image Garden data, use Studio. Use Playview when you want the Image Garden app experience that composes both.
