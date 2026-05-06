# Constellation Desktop

Thin Electron shell for the local backend and existing web viewer.

What it does:

- starts `constellation-backend` in the app data directory;
- loads the backend's web runtime page (or `CONSTELLATION_VIEWER_URL`);
- exposes an **Import Folder…** menu item that calls `POST /api/import/folder`;
- keeps viewer rendering in `@constellation/viewer` instead of forking renderer code.

Development:

```bash
pnpm studio:sync
pnpm --filter @constellation/viewer build
pnpm --filter @constellation/desktop dev
```

Useful overrides:

- `CONSTELLATION_BACKEND_URL=http://127.0.0.1:8766/` connects to an existing backend.
- `CONSTELLATION_VIEWER_URL=http://127.0.0.1:5173/demo/` loads a separate frontend/viewer URL.
- `CONSTELLATION_BACKEND_COMMAND` / `CONSTELLATION_BACKEND_ARGS` override backend process startup.
