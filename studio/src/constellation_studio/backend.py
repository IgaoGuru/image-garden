"""Local backend API for Constellation desktop/runtime clients."""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import sys
import threading
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import ClassVar, cast
from urllib.parse import parse_qs, unquote, urlsplit

from constellation_studio.index_store import IndexStore
from constellation_studio.indexing import default_indexing_paths, import_folder

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000

BACKEND_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Constellation Desktop Backend</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    body { margin: 0; background: #050507; color: #f4f0e8; overflow: hidden; }
    header { height: 57px; padding: 0 20px; border-bottom: 1px solid #2a2622; display: flex; gap: 16px; align-items: center; position: relative; z-index: 3; background: rgba(5, 5, 7, 0.92); backdrop-filter: blur(14px); }
    h1 { font-size: 18px; margin: 0; }
    #status { color: #c0b7aa; font-size: 13px; flex: 1; }
    button { border: 1px solid #4d4135; background: #1d1813; color: #f4f0e8; border-radius: 10px; padding: 9px 12px; font: inherit; cursor: pointer; }
    button.primary { background: #f6c177; color: #21170d; border-color: #f6c177; font-weight: 700; }
    button:disabled { opacity: 0.45; cursor: not-allowed; }
    input { width: 100%; box-sizing: border-box; border: 1px solid #4d4135; background: #0c0a09; color: #f4f0e8; border-radius: 10px; padding: 10px 12px; font: inherit; }
    code { color: #f6c177; }
    #viewer { width: 100vw; height: calc(100vh - 58px); position: relative; }
    #add-source { display: none; }
    body.has-assets #add-source { display: inline-flex; }
    #onboarding { position: fixed; inset: 58px 0 0; z-index: 2; display: none; align-items: center; justify-content: center; padding: 28px; background: radial-gradient(circle at 30% 20%, rgba(246, 193, 119, 0.12), transparent 36%), rgba(5, 5, 7, 0.72); backdrop-filter: blur(10px); overflow: auto; }
    #onboarding.visible { display: flex; }
    .panel { width: min(1080px, 100%); background: rgba(18, 16, 14, 0.94); border: 1px solid #342d26; border-radius: 24px; padding: 28px; box-shadow: 0 24px 80px rgba(0, 0, 0, 0.45); }
    .panel h2 { margin: 0 0 8px; font-size: clamp(28px, 4vw, 44px); letter-spacing: -0.04em; }
    .panel > p { margin: 0 0 24px; color: #cfc5b8; max-width: 780px; line-height: 1.55; }
    .source-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
    .source-choice { text-align: left; border-radius: 14px; padding: 13px 14px; min-height: 76px; display: grid; gap: 4px; background: #0c0a09; }
    .source-choice strong { font-size: 15px; }
    .source-choice span { color: #9e9286; font-size: 12px; line-height: 1.3; }
    .source-choice.selected { border-color: #f6c177; background: rgba(246, 193, 119, 0.12); }
    .source-detail { display: none; border: 1px solid #3a3129; border-radius: 16px; padding: 16px; background: #0c0a09; }
    .source-detail.visible { display: grid; gap: 12px; }
    .source-detail h3 { margin: 0; font-size: 18px; }
    .source-detail p { color: #c0b7aa; line-height: 1.45; margin: 0; }
    .source-detail .actions { display: flex; flex-wrap: wrap; gap: 10px; }
    .manual-folder { display: none; grid-template-columns: minmax(240px, 1fr) auto; gap: 8px; }
    .manual-folder.visible { display: grid; }
    .badge { align-self: flex-start; color: #f6c177; background: rgba(246, 193, 119, 0.12); border: 1px solid rgba(246, 193, 119, 0.26); border-radius: 999px; padding: 4px 9px; font-size: 12px; }
    .muted { color: #8f857a; font-size: 12px; }
    .fallback { padding: 20px; overflow: auto; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 14px; }
    .card { background: #12100e; border: 1px solid #302a24; border-radius: 12px; overflow: hidden; }
    .card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: #090807; }
    .card div { padding: 8px; font-size: 12px; color: #cfc5b8; overflow-wrap: anywhere; }
    @media (max-width: 860px) { .source-grid { grid-template-columns: 1fr; } body { overflow: auto; } }
  </style>
</head>
<body>
  <header>
    <h1>Constellation</h1>
    <div id="status">Loading library…</div>
    <button id="add-source" type="button">Add Source…</button>
  </header>
  <main id="viewer"></main>
  <section id="onboarding" aria-live="polite"></section>
  <script type="module">
    const status = document.querySelector('#status');
    const root = document.querySelector('#viewer');
    const onboarding = document.querySelector('#onboarding');
    const addSourceButton = document.querySelector('#add-source');
    const desktop = window.constellationDesktop;

    const [payload, sourcesPayload] = await Promise.all([
      fetch('/api/assets?limit=5000').then((response) => {
        if (!response.ok) throw new Error(`assets HTTP ${response.status}`);
        return response.json();
      }),
      fetch('/api/sources').then((response) => response.ok ? response.json() : { sources: [] }),
    ]);
    const assets = payload.assets ?? [];
    document.body.classList.toggle('has-assets', assets.length > 0);
    const data = {
      images: assets.map((asset) => ({
        ...asset,
        url: asset.fullUrl ?? asset.thumbnailUrl,
      })),
    };

    async function importViewer() {
      const candidates = [
        '/viewer-entry.js',
        '/viewer/constellation-viewer.js',
        '/viewer/constellation-viewer.es.js',
        '/viewer/constellation-viewer.mjs',
        '/viewer/viewer.js',
        '/viewer/viewer.mjs',
        '/viewer/index.js',
        '/viewer/index.mjs',
      ];
      for (const url of candidates) {
        try { return await import(url); } catch (_) {}
      }
      return null;
    }

    const viewer = await importViewer();
    if (viewer && typeof viewer.mount === 'function') {
      status.textContent = assets.length
        ? `Exploring ${assets.length} positioned assets.`
        : 'Choose a source to start building your local library.';
      viewer.mount(root, data, { backgroundColor: 0x050507, sprites: { renderMode: 'auto' } });
    } else {
      status.textContent = 'Viewer bundle not found; local API is running.';
      root.className = 'fallback';
      root.innerHTML = '<p>Local API is running, but the @constellation/viewer bundle was not found. Run <code>pnpm --filter @constellation/viewer build</code> or start the desktop app with a valid viewer dist.</p><div class="grid"></div>';
      renderFallbackGrid(assets);
    }

    if (assets.length === 0) showOnboarding();
    addSourceButton.addEventListener('click', showOnboarding);

    function showOnboarding() {
      onboarding.innerHTML = onboardingMarkup(sourcesPayload.sources ?? []);
      onboarding.classList.add('visible');
      onboarding.querySelector('[data-action="close"]')?.addEventListener('click', () => onboarding.classList.remove('visible'));
      onboarding.querySelectorAll('[data-source-choice]').forEach((button) => {
        button.addEventListener('click', () => selectSource(button.dataset.sourceChoice));
      });
      onboarding.querySelector('[data-action="choose-folder"]')?.addEventListener('click', chooseFolder);
      const manualForm = onboarding.querySelector('[data-manual-folder]');
      onboarding.querySelector('[data-action="show-manual-folder"]')?.addEventListener('click', () => manualForm?.classList.toggle('visible'));
      manualForm?.addEventListener('submit', submitManualFolder);
    }

    function selectSource(sourceType) {
      onboarding.querySelectorAll('[data-source-choice]').forEach((button) => {
        button.classList.toggle('selected', button.dataset.sourceChoice === sourceType);
      });
      onboarding.querySelectorAll('[data-source-detail]').forEach((detail) => {
        detail.classList.toggle('visible', detail.dataset.sourceDetail === sourceType);
      });
    }

    function onboardingMarkup(sources) {
      const source = (type) => sources.find((item) => item.type === type) ?? {};
      const folder = source('folder');
      const studio = source('studioDataset');
      const apple = source('applePhotos');
      const canUseDesktopPicker = Boolean(desktop?.openImportFolder);
      return `<div class="panel">
        <button style="float:right" type="button" data-action="close">Close</button>
        <span class="badge">Local-first setup</span>
        <h2>Choose your photo source</h2>
        <p>Pick a source first. Import controls and paths stay hidden until you choose one.</p>
        <div class="source-grid" role="list" aria-label="Photo source choices">
          <button class="source-choice" type="button" data-source-choice="folder">
            <strong>${folder.label ?? 'Image folder'}</strong>
            <span>Ready now</span>
          </button>
          <button class="source-choice" type="button" data-source-choice="studioDataset">
            <strong>${studio.label ?? 'Existing Studio dataset'}</strong>
            <span>Coming soon</span>
          </button>
          <button class="source-choice" type="button" data-source-choice="applePhotos">
            <strong>${apple.label ?? 'iCloud / Apple Photos'}</strong>
            <span>Coming soon</span>
          </button>
        </div>
        <section class="source-detail" data-source-detail="folder">
          <span class="badge">Ready</span>
          <h3>${folder.label ?? 'Image folder'}</h3>
          <p>${folder.description ?? 'Recursively import JPEG, PNG, HEIC/HEIF where supported, and other image files from a directory.'}</p>
          <div class="actions">
            <button class="primary" type="button" data-action="choose-folder">${canUseDesktopPicker ? 'Choose Folder…' : 'Enter folder path'}</button>
            ${canUseDesktopPicker ? '<button type="button" data-action="show-manual-folder">Enter path manually</button>' : ''}
          </div>
          <form class="manual-folder" data-manual-folder>
            <input name="path" placeholder="/absolute/path/to/photos" autocomplete="off">
            <button type="submit">Import path</button>
          </form>
        </section>
        <section class="source-detail" data-source-detail="studioDataset">
          <span class="badge">Coming soon</span>
          <h3>${studio.label ?? 'Existing Studio dataset'}</h3>
          <p>${studio.description ?? 'Use an already computed constellation.json / Studio manifest with image and embedding/layout assets.'}</p>
          <div class="actions"><button type="button" disabled>Importer not wired yet</button><span class="muted">Needs POST /api/import/studio normalization.</span></div>
        </section>
        <section class="source-detail" data-source-detail="applePhotos">
          <span class="badge">Coming soon</span>
          <h3>${apple.label ?? 'iCloud / Apple Photos'}</h3>
          <p>${apple.description ?? 'Import from macOS Photos/iCloud through a native PhotoKit adapter after permissions are implemented.'}</p>
          <div class="actions"><button type="button" disabled>Requires PhotoKit bridge</button><span class="muted">Not available in this prototype.</span></div>
        </section>
      </div>`;
    }

    async function chooseFolder() {
      if (desktop?.openImportFolder) {
        const result = await desktop.openImportFolder();
        if (result?.ok) window.location.reload();
        return;
      }
      onboarding.querySelector('[data-manual-folder]')?.classList.add('visible');
      onboarding.querySelector('[name="path"]')?.focus();
    }

    async function submitManualFolder(event) {
      event.preventDefault();
      const form = event.currentTarget;
      const path = new FormData(form).get('path');
      if (typeof path !== 'string' || !path.trim()) return;
      status.textContent = `Importing ${path}…`;
      const response = await fetch('/api/import/folder', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        status.textContent = `Import failed: ${result.error ?? response.statusText}`;
        return;
      }
      window.location.reload();
    }

    function renderFallbackGrid(assets) {
      const grid = root.querySelector('.grid');
      if (!grid) return;
      for (const asset of assets) {
        const card = document.createElement('article');
        card.className = 'card';
        const img = document.createElement('img');
        img.loading = 'lazy';
        img.src = asset.thumbnailUrl;
        img.alt = asset.id;
        const caption = document.createElement('div');
        caption.textContent = asset.metadata?.sourcePath ?? asset.id;
        card.append(img, caption);
        grid.append(card);
      }
    }
  </script>
</body>
</html>
"""


@dataclass(frozen=True, slots=True)
class BackendConfig:
    """Local backend configuration."""

    host: str
    port: int
    data_dir: Path
    viewer_dist: Path | None = None


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with reusable sockets."""

    allow_reuse_address: bool = True


class BackendRequestHandler(BaseHTTPRequestHandler):
    """Request handler configured by ``make_handler``."""

    store: ClassVar[IndexStore]
    asset_root: ClassVar[Path]
    viewer_dist: ClassVar[Path | None]

    def do_OPTIONS(self) -> None:
        """Serve CORS preflight requests for local development."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        """Serve a GET request."""
        self._serve_get(send_body=True)

    def do_HEAD(self) -> None:
        """Serve a HEAD request."""
        self._serve_get(send_body=False)

    def do_POST(self) -> None:
        """Serve local API mutation requests."""
        route = urlsplit(self.path).path
        try:
            if route == "/api/import/folder":
                self._post_import_folder()
                return
            if route == "/api/index/start":
                self.store.set_index_state("idle")
                self._send_json({"ok": True, "status": self.store.status()})
                return
            if route == "/api/index/pause":
                self.store.set_paused(paused=True)
                self._send_json({"ok": True, "status": self.store.status()})
                return
            if route == "/api/index/resume":
                self.store.set_paused(paused=False)
                self._send_json({"ok": True, "status": self.store.status()})
                return
        except (
            OSError,
            RuntimeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self._send_json(
                {"ok": False, "error": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Write compact access logs to stderr."""
        print(f"{self.address_string()} - {format % args}", file=sys.stderr)

    def _serve_get(self, *, send_body: bool) -> None:  # noqa: PLR0911
        split = urlsplit(self.path)
        route = split.path
        query = parse_qs(split.query)
        if route in {"/", "/index.html"}:
            self._send_bytes(
                BACKEND_INDEX_HTML.encode("utf-8"),
                "text/html; charset=utf-8",
                send_body=send_body,
            )
            return
        if route == "/api/status":
            self._send_json(self.store.status(), send_body=send_body)
            return
        if route == "/api/sources":
            self._send_json(source_capabilities(), send_body=send_body)
            return
        if route == "/api/assets":
            self._get_assets(query, send_body=send_body)
            return
        if route == "/api/assets/near":
            self._get_near_assets(query, send_body=send_body)
            return
        if route.startswith("/api/assets/"):
            self._get_asset(
                route.removeprefix("/api/assets/"), send_body=send_body
            )
            return
        if route.startswith("/api/thumbnails/"):
            self._send_asset_file(
                route.removeprefix("/api/thumbnails/"),
                thumbnail=True,
                send_body=send_body,
            )
            return
        if route.startswith("/api/files/"):
            self._send_asset_file(
                route.removeprefix("/api/files/"),
                thumbnail=False,
                send_body=send_body,
            )
            return
        if route == "/viewer-entry.js" and self.viewer_dist is not None:
            self._send_viewer_entry(send_body=send_body)
            return
        if route.startswith("/viewer/") and self.viewer_dist is not None:
            path = resolve_below(
                self.viewer_dist,
                route.removeprefix("/viewer/"),
            )
            self._send_path_or_404(path, send_body=send_body)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def _post_import_folder(self) -> None:
        payload = self._read_json_body()
        folder_obj = payload.get("path")
        if not isinstance(folder_obj, str) or not folder_obj:
            msg = "JSON body must include a non-empty string path"
            raise ValueError(msg)
        result = import_folder(
            Path(folder_obj),
            store=self.store,
            asset_root=self.asset_root,
        )
        self._send_json(
            {
                "ok": True,
                "imported": result.imported,
                "totalAssets": result.total_assets,
                "sourceType": result.source_type,
                "sourceId": result.source_id,
                "folder": str(result.folder),
                "status": self.store.status(),
            },
        )

    def _get_assets(
        self,
        query: Mapping[str, list[str]],
        *,
        send_body: bool,
    ) -> None:
        limit = bounded_int(query, "limit", default=DEFAULT_LIMIT)
        offset = max(
            0, bounded_int(query, "offset", default=0, upper=1_000_000_000)
        )
        assets = self.store.list_assets(limit=limit, offset=offset)
        self._send_json(
            {
                "assets": assets,
                "limit": limit,
                "offset": offset,
                "total": self.store.count_assets(),
            },
            send_body=send_body,
        )

    def _get_near_assets(
        self,
        query: Mapping[str, list[str]],
        *,
        send_body: bool,
    ) -> None:
        x = query_float(query, "x", 0.0)
        y = query_float(query, "y", 0.0)
        z = query_float(query, "z", 0.0)
        radius = query_float(query, "radius", 50.0)
        limit = bounded_int(query, "limit", default=DEFAULT_LIMIT)
        assets = self.store.nearby_assets(
            point=(x, y, z), radius=radius, limit=limit
        )
        self._send_json(
            {"assets": assets, "total": len(assets)}, send_body=send_body
        )

    def _get_asset(self, raw_id: str, *, send_body: bool) -> None:
        asset = self.store.get_asset(unquote(raw_id))
        if asset is None:
            self.send_error(HTTPStatus.NOT_FOUND, "asset not found")
            return
        self._send_json(asset, send_body=send_body)

    def _send_asset_file(
        self,
        raw_id: str,
        *,
        thumbnail: bool,
        send_body: bool,
    ) -> None:
        asset_id = unquote(raw_id)
        path = (
            self.store.asset_thumbnail_path(asset_id)
            if thumbnail
            else self.store.asset_file_path(asset_id)
        )
        self._send_path_or_404(path, send_body=send_body)

    def _send_viewer_entry(self, *, send_body: bool) -> None:
        if self.viewer_dist is None:
            self.send_error(HTTPStatus.NOT_FOUND, "viewer dist not configured")
            return
        entry = find_viewer_entry_file(self.viewer_dist)
        if entry is None:
            self.send_error(HTTPStatus.NOT_FOUND, "viewer entry not found")
            return
        relative = entry.relative_to(self.viewer_dist)
        module = (
            f"export * from {json.dumps('/viewer/' + relative.as_posix())};\n"
        )
        self._send_bytes(
            module.encode("utf-8"),
            "text/javascript; charset=utf-8",
            send_body=send_body,
        )

    def _send_path_or_404(self, path: Path | None, *, send_body: bool) -> None:
        if path is None or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        content_type = (
            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        self._send_bytes(path.read_bytes(), content_type, send_body=send_body)

    def _send_json(
        self,
        payload: object,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        send_body: bool = True,
    ) -> None:
        self._send_bytes(
            json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n",
            "application/json; charset=utf-8",
            status=status,
            send_body=send_body,
        )

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        send_body: bool,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _read_json_body(self) -> Mapping[str, object]:
        length_header = self.headers.get("Content-Length", "0")
        length = int(length_header)
        loaded: object = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(loaded, Mapping):
            msg = "request body must be a JSON object"
            raise ValueError(msg)
        return cast("Mapping[str, object]", loaded)


def bounded_int(
    query: Mapping[str, list[str]],
    key: str,
    *,
    default: int,
    upper: int = MAX_LIMIT,
) -> int:
    """Parse a bounded positive integer query parameter."""
    raw = query.get(key, [str(default)])[0]
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return min(max(parsed, 0), upper)


def query_float(
    query: Mapping[str, list[str]],
    key: str,
    default: float,
) -> float:
    """Parse a float query parameter."""
    raw = query.get(key, [str(default)])[0]
    try:
        return float(raw)
    except ValueError:
        return default


def source_capabilities() -> dict[str, object]:
    """Return source types surfaced by the prototype onboarding UI."""
    return {
        "sources": [
            {
                "type": "folder",
                "label": "Image folder",
                "enabled": True,
                "importEndpoint": "/api/import/folder",
                "description": "Import images recursively from a local directory.",
            },
            {
                "type": "studioDataset",
                "label": "Existing Studio dataset",
                "enabled": False,
                "reason": "POST /api/import/studio is not implemented yet.",
                "description": "Use an already computed constellation.json / Studio manifest.",
            },
            {
                "type": "applePhotos",
                "label": "iCloud / Apple Photos",
                "enabled": False,
                "reason": "Requires a native PhotoKit source adapter and permissions flow.",
                "description": "Import from macOS Photos/iCloud after native integration exists.",
            },
        ],
    }


def resolve_below(root: Path, route_tail: str) -> Path | None:
    """Resolve a URL tail below a trusted root, rejecting traversal."""
    resolved_root = root.expanduser().resolve()
    decoded = unquote(route_tail)
    parts = [part for part in decoded.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return None
    candidate = resolved_root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(resolved_root):
        return None
    return candidate


def find_default_viewer_dist(start: Path) -> Path | None:
    """Find packages/viewer/dist from a repo descendant."""
    for base in [start, *start.parents]:
        candidate = base / "packages" / "viewer" / "dist"
        if candidate.is_dir():
            return candidate
    return None


def find_viewer_entry_file(viewer_dist: Path) -> Path | None:
    """Return the likely ESM viewer entry file."""
    names = [
        "constellation-viewer.js",
        "constellation-viewer.es.js",
        "constellation-viewer.mjs",
        "viewer.js",
        "viewer.mjs",
        "index.js",
        "index.mjs",
    ]
    for name in names:
        candidate = viewer_dist / name
        if candidate.is_file():
            return candidate
    candidates = sorted(
        [
            path
            for pattern in ("*.mjs", "*.js")
            for path in viewer_dist.glob(pattern)
            if path.is_file()
        ],
        key=lambda path: path.name,
    )
    return candidates[0] if candidates else None


def make_handler(
    *,
    store: IndexStore,
    asset_root: Path,
    viewer_dist: Path | None,
) -> type[BackendRequestHandler]:
    """Return a request handler bound to a store and paths."""

    class ConfiguredBackendRequestHandler(BackendRequestHandler):
        pass

    ConfiguredBackendRequestHandler.store = store
    ConfiguredBackendRequestHandler.asset_root = (
        asset_root.expanduser().resolve()
    )
    ConfiguredBackendRequestHandler.viewer_dist = (
        viewer_dist.expanduser().resolve() if viewer_dist is not None else None
    )
    return ConfiguredBackendRequestHandler


def server_url(server: TCPServer, host: str) -> str:
    """Return the URL for a bound HTTP server."""
    port = int(server.server_address[1])
    return f"http://{host}:{port}/"


def build_parser() -> argparse.ArgumentParser:
    """Build the backend CLI parser."""
    parser = argparse.ArgumentParser(
        description="Run the Constellation local backend API."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host.")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Bind port; use 0 for ephemeral.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".constellation-backend"),
        help="App data directory for SQLite and generated assets.",
    )
    parser.add_argument(
        "--viewer-dist",
        type=Path,
        default=None,
        help="Optional built @constellation/viewer dist directory.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Run the local backend until interrupted."""
    viewer_dist_arg = cast("Path | None", args.viewer_dist)
    config = BackendConfig(
        host=str(args.host),
        port=int(args.port),
        data_dir=Path(args.data_dir),
        viewer_dist=viewer_dist_arg,
    )
    paths = default_indexing_paths(config.data_dir)
    viewer_dist = config.viewer_dist or find_default_viewer_dist(Path.cwd())
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    handler = make_handler(
        store=store,
        asset_root=paths.asset_root,
        viewer_dist=viewer_dist,
    )
    with QuietThreadingHTTPServer(
        (config.host, config.port), handler
    ) as httpd:
        url = server_url(httpd, config.host)
        print(f"Constellation backend listening at {url}", flush=True)
        print(f"SQLite: {paths.db_path}", flush=True)
        print(f"Assets: {paths.asset_root}", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


@contextlib.contextmanager
def run_test_backend(
    *,
    data_dir: Path,
    viewer_dist: Path | None = None,
) -> Generator[str]:
    """Start an ephemeral backend server for tests."""
    paths = default_indexing_paths(data_dir)
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    handler = make_handler(
        store=store,
        asset_root=paths.asset_root,
        viewer_dist=viewer_dist,
    )
    httpd = QuietThreadingHTTPServer((DEFAULT_HOST, 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield server_url(httpd, DEFAULT_HOST)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
