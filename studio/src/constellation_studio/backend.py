"""Local backend API for Constellation desktop/runtime clients."""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import platform
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import ClassVar, cast
from urllib.parse import parse_qs, unquote, urlsplit

from constellation_studio.embed import DEFAULT_MODEL, DEFAULT_PRETRAINED
from constellation_studio.embedding_providers import (
    EmbeddingProvider,
    create_embedding_provider,
)
from constellation_studio.index_store import IndexStore
from constellation_studio.indexing import (
    ImportResult,
    default_indexing_paths,
    import_folder,
    import_studio_dataset,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000

BACKEND_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Constellation</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: radial-gradient(circle at top left, #1a141f 0, #08080b 38%, #020203 100%); color: #faf7f0; overflow: hidden; }
    header { height: 60px; padding: 0 20px; border-bottom: 1px solid rgba(255,255,255,0.09); display: flex; gap: 16px; align-items: center; position: relative; z-index: 3; background: rgba(3, 3, 5, 0.82); backdrop-filter: blur(18px); }
    h1 { font-size: 18px; margin: 0; letter-spacing: -0.02em; }
    #status { color: #b7b0a6; font-size: 13px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    button { border: 1px solid rgba(255,255,255,0.18); background: rgba(255,255,255,0.06); color: #fffaf0; border-radius: 999px; padding: 10px 14px; font: inherit; cursor: pointer; transition: transform .15s ease, border-color .15s ease, background .15s ease; }
    button:hover:not(:disabled) { transform: translateY(-1px); border-color: rgba(255,255,255,0.42); background: rgba(255,255,255,0.11); }
    button.primary { background: #f8efe0; color: #08080b; border-color: #f8efe0; font-weight: 750; }
    button:disabled { opacity: 0.45; cursor: not-allowed; }
    input { width: 100%; border: 1px solid rgba(255,255,255,0.18); background: rgba(0,0,0,0.35); color: #fff; border-radius: 14px; padding: 12px 14px; font: inherit; }
    code { color: #ffd8a8; }
    #viewer { width: 100vw; height: calc(100vh - 60px); position: relative; }
    #add-source { display: none; }
    body.has-assets #add-source { display: inline-flex; }
    #onboarding { position: fixed; inset: 60px 0 0; z-index: 2; display: none; align-items: center; justify-content: center; padding: 32px; background: radial-gradient(circle at 50% 0%, rgba(90, 60, 130, .32), rgba(0, 0, 0, .82) 42%); backdrop-filter: blur(12px); overflow: auto; }
    #onboarding.visible { display: flex; }
    .panel { width: min(1120px, 100%); background: linear-gradient(180deg, rgba(18,18,24,.96), rgba(7,7,10,.98)); border: 1px solid rgba(255,255,255,0.12); border-radius: 30px; padding: clamp(22px, 4vw, 40px); box-shadow: 0 30px 100px rgba(0,0,0,.72); }
    .panel-top { display: flex; align-items: flex-start; gap: 18px; justify-content: space-between; margin-bottom: 24px; }
    .eyebrow { color: #ffd8a8; font-size: 12px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; margin-bottom: 10px; }
    .panel h2 { margin: 0 0 10px; font-size: clamp(34px, 5vw, 64px); line-height: .95; letter-spacing: -0.06em; max-width: 780px; }
    .panel p { margin: 0; color: #c9c0b6; line-height: 1.55; }
    .intro { max-width: 760px; font-size: 16px; }
    .source-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin-top: 28px; }
    .source-card { text-align: left; border-radius: 24px; padding: 22px; min-height: 260px; display: grid; align-content: space-between; gap: 18px; background: rgba(255,255,255,0.045); border: 1px solid rgba(255,255,255,0.12); }
    .source-card:hover { background: rgba(255,255,255,0.075); }
    .source-card h3 { margin: 0 0 8px; font-size: 26px; letter-spacing: -0.03em; }
    .source-card p { color: #bdb4aa; }
    .badge { display: inline-flex; align-items: center; width: fit-content; padding: 5px 9px; border-radius: 999px; font-size: 12px; font-weight: 800; letter-spacing: .02em; background: rgba(81, 207, 102, .16); color: #b2f2bb; border: 1px solid rgba(81, 207, 102, .26); }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .manual-form { display: none; grid-template-columns: minmax(240px, 1fr) auto; gap: 10px; margin-top: 12px; }
    .manual-form.visible { display: grid; }
    .hint { color: #8f877d; font-size: 12px; margin-top: 10px; }
    .fallback { padding: 20px; overflow: auto; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 14px; }
    .card { background: #080808; border: 1px solid #333; border-radius: 12px; overflow: hidden; }
    .card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: #000; }
    .card div { padding: 8px; font-size: 12px; color: #bbb; overflow-wrap: anywhere; }
    @media (max-width: 820px) { body { overflow: auto; } .source-grid { grid-template-columns: 1fr; } .manual-form { grid-template-columns: 1fr; } .panel-top { display: block; } .panel-top button { margin-top: 16px; } }
  </style>
</head>
<body>
  <header>
    <h1>Constellation</h1>
    <div id="status">Loading library…</div>
    <button id="add-source" type="button">Add Photos…</button>
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
    const sources = sourcesPayload.sources ?? [];
    document.body.classList.toggle('has-assets', assets.length > 0);
    const data = { images: assets.map((asset) => ({ ...asset, url: asset.fullUrl ?? asset.thumbnailUrl })) };

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
        ? `Exploring ${assets.length} local photo${assets.length === 1 ? '' : 's'}.`
        : 'Bring your own photos to start a local constellation.';
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
      onboarding.innerHTML = onboardingMarkup();
      onboarding.classList.add('visible');
      onboarding.querySelector('[data-action="close"]')?.addEventListener('click', () => onboarding.classList.remove('visible'));
      onboarding.querySelector('[data-action="choose-folder"]')?.addEventListener('click', chooseFolder);
      onboarding.querySelector('[data-action="choose-studio"]')?.addEventListener('click', chooseStudioDataset);
      onboarding.querySelectorAll('[data-action="show-manual"]').forEach((button) => {
        button.addEventListener('click', () => {
          const form = onboarding.querySelector(`[data-manual-form="${button.dataset.target}"]`);
          form?.classList.toggle('visible');
          form?.querySelector('input')?.focus();
        });
      });
      onboarding.querySelector('[data-manual-form="folder"]')?.addEventListener('submit', submitManualFolder);
      onboarding.querySelector('[data-manual-form="studio"]')?.addEventListener('submit', submitManualStudio);
    }

    function source(type) {
      return sources.find((item) => item.type === type) ?? {};
    }

    function onboardingMarkup() {
      const folder = source('folder');
      const studio = source('studioDataset');
      const canPickFolder = true;
      const canPickStudio = true;
      const close = assets.length > 0 ? '<button type="button" data-action="close">Close</button>' : '';
      return `<div class="panel">
        <div class="panel-top">
          <div>
            <div class="eyebrow">Bring your own photos</div>
            <h2>Build a constellation from files you control.</h2>
            <p class="intro">Start with a local directory of images, a photo export, or a portable Constellation Studio dataset. Everything is file-based and local-first.</p>
          </div>
          ${close}
        </div>
        <div class="source-grid">
          <article class="source-card">
            <div>
              <span class="badge">Ready now</span>
              <h3>${folder.label ?? 'Photo directory'}</h3>
              <p>${folder.description ?? 'Recursively import supported image files from a folder on this machine.'}</p>
            </div>
            <div>
              <div class="actions">
                <button class="primary" type="button" data-action="choose-folder">${canPickFolder ? 'Choose directory…' : 'Enter directory path'}</button>
                ${canPickFolder ? '<button type="button" data-action="show-manual" data-target="folder">Paste path</button>' : ''}
              </div>
              <form class="manual-form" data-manual-form="folder">
                <input name="path" placeholder="/absolute/path/to/photos" autocomplete="off">
                <button type="submit">Import</button>
              </form>
              <div class="hint">Good for camera dumps, Finder folders, and photo exports.</div>
            </div>
          </article>
          <article class="source-card">
            <div>
              <span class="badge">Ready now</span>
              <h3>${studio.label ?? 'Constellation Studio dataset'}</h3>
              <p>${studio.description ?? 'Open a constellation.json or constellation.studio.json produced by Studio.'}</p>
            </div>
            <div>
              <div class="actions">
                <button class="primary" type="button" data-action="choose-studio">${canPickStudio ? 'Open dataset…' : 'Enter dataset path'}</button>
                ${canPickStudio ? '<button type="button" data-action="show-manual" data-target="studio">Paste path</button>' : ''}
              </div>
              <form class="manual-form" data-manual-form="studio">
                <input name="path" placeholder="/path/to/constellation.json or constellation.studio.json" autocomplete="off">
                <button type="submit">Import</button>
              </form>
              <div class="hint">Best for precomputed/portable Studio sets. Existing assets are referenced in place.</div>
            </div>
          </article>
        </div>
      </div>`;
    }

    async function chooseFolder() {
      if (desktop?.openImportFolder) {
        await runDesktopImport(() => desktop.openImportFolder(), 'Importing directory…');
        return;
      }
      const path = await chooseBackendPath('/api/dialog/folder', 'Choose a directory, then return to this browser window.');
      if (path) {
        await startFolderImport(path);
        return;
      }
      onboarding.querySelector('[data-manual-form="folder"]')?.classList.add('visible');
      onboarding.querySelector('[data-manual-form="folder"] input')?.focus();
    }

    async function chooseStudioDataset() {
      if (desktop?.openImportStudio) {
        await runDesktopImport(() => desktop.openImportStudio(), 'Importing Studio dataset…');
        return;
      }
      const path = await chooseBackendPath('/api/dialog/studio', 'Choose a Studio dataset, then return to this browser window.');
      if (path) {
        await submitImportPath('/api/import/studio', path, 'Importing Studio dataset');
        return;
      }
      onboarding.querySelector('[data-manual-form="studio"]')?.classList.add('visible');
      onboarding.querySelector('[data-manual-form="studio"] input')?.focus();
    }

    async function chooseBackendPath(endpoint, message) {
      status.textContent = message;
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: '{}',
        });
        const result = await response.json();
        if (response.ok && result.ok && typeof result.path === 'string') return result.path;
        status.textContent = 'No path selected. You can paste a path manually.';
        return null;
      } catch (error) {
        status.textContent = `Could not open picker: ${error instanceof Error ? error.message : String(error)}`;
        return null;
      }
    }

    async function runDesktopImport(importer, message) {
      status.textContent = message;
      try {
        const result = await importer();
        if (result?.ok) window.location.reload();
        else if (!result?.canceled) status.textContent = result?.error ?? 'Import canceled.';
      } catch (error) {
        status.textContent = `Import failed: ${error instanceof Error ? error.message : String(error)}`;
      }
    }

    async function submitManualFolder(event) {
      event.preventDefault();
      const path = new FormData(event.currentTarget).get('path');
      if (typeof path !== 'string' || !path.trim()) return;
      await startFolderImport(path);
    }

    async function submitManualStudio(event) {
      event.preventDefault();
      const path = new FormData(event.currentTarget).get('path');
      if (typeof path !== 'string' || !path.trim()) return;
      await submitImportPath('/api/import/studio', path, 'Importing Studio dataset');
    }

    async function startFolderImport(path) {
      status.textContent = `Importing directory: ${path}…`;
      const response = await fetch('/api/import/folder', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ path, background: true }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        status.textContent = `Import failed: ${result.error ?? response.statusText}`;
        return;
      }
      onboarding.classList.remove('visible');
      await pollImportProgress();
    }

    async function submitImportPath(endpoint, path, label) {
      status.textContent = `${label}: ${path}…`;
      const response = await fetch(endpoint, {
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

    async function pollImportProgress() {
      while (true) {
        await new Promise((resolve) => setTimeout(resolve, 900));
        const current = await fetch('/api/status').then((response) => response.json());
        const completed = current.jobCompleted ?? 0;
        const total = current.jobTotal ?? 0;
        const phase = current.jobPhase ?? current.state ?? 'working';
        const message = current.jobMessage ? ` — ${current.jobMessage}` : '';
        status.textContent = total > 0
          ? `${phase}: ${completed}/${total}${message}`
          : `${phase}${message}`;
        if (current.state === 'error' || phase === 'error') return;
        if ((phase === 'ready' || current.state === 'idle') && current.totalAssets > 0) {
          window.location.reload();
          return;
        }
      }
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
    embedding_engine: str = "none"
    embedding_model: str = DEFAULT_MODEL
    embedding_pretrained: str = DEFAULT_PRETRAINED
    embedding_device: str = "auto"
    embedding_batch_size: int = 32
    onnx_model: Path | None = None
    onnx_provider: str = "auto"


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with reusable sockets."""

    allow_reuse_address: bool = True


class BackendRequestHandler(BaseHTTPRequestHandler):
    """Request handler configured by ``make_handler``."""

    store: ClassVar[IndexStore]
    asset_root: ClassVar[Path]
    viewer_dist: ClassVar[Path | None]
    embedding_provider: ClassVar[EmbeddingProvider | None]
    embedding_batch_size: ClassVar[int]
    import_lock: ClassVar[threading.Lock]
    import_thread: ClassVar[threading.Thread | None]

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

    def do_POST(self) -> None:  # noqa: PLR0911
        """Serve local API mutation requests."""
        route = urlsplit(self.path).path
        try:
            if route == "/api/import/folder":
                self._post_import_folder()
                return
            if route == "/api/import/studio":
                self._post_import_studio()
                return
            if route == "/api/dialog/folder":
                self._post_dialog_folder()
                return
            if route == "/api/dialog/studio":
                self._post_dialog_studio()
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

    def _serve_get(self, *, send_body: bool) -> None:  # noqa: C901, PLR0911
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
        if payload.get("background") is True:
            self._start_background_import_folder(Path(folder_obj))
            self._send_json(
                {
                    "ok": True,
                    "started": True,
                    "status": self.store.status(),
                },
            )
            return
        result = self._run_import_folder(Path(folder_obj))
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

    def _run_import_folder(self, folder: Path) -> ImportResult:
        return import_folder(
            folder,
            store=self.store,
            asset_root=self.asset_root,
            embedding_provider=self.embedding_provider,
            batch_size=self.embedding_batch_size,
        )

    def _start_background_import_folder(self, folder: Path) -> None:
        with self.import_lock:
            if (
                self.import_thread is not None
                and self.import_thread.is_alive()
            ):
                msg = "an import job is already running"
                raise RuntimeError(msg)
            self.store.set_index_state("importing")
            self.store.set_job_progress(
                phase="queued",
                completed=0,
                total=0,
                message=f"Queued import for {folder}",
            )
            thread = threading.Thread(
                target=self._background_import_folder,
                args=(folder,),
                daemon=True,
            )
            type(self).import_thread = thread
            thread.start()

    def _background_import_folder(self, folder: Path) -> None:
        try:
            self._run_import_folder(folder)
        except (OSError, RuntimeError, ValueError) as exc:
            self.store.set_index_state("error")
            self.store.set_job_progress(
                phase="error",
                completed=0,
                total=0,
                message=str(exc),
            )
        finally:
            with self.import_lock:
                type(self).import_thread = None

    def _post_dialog_folder(self) -> None:
        _ = self._read_json_body()
        path = choose_folder_dialog()
        self._send_json(
            {"ok": path is not None, "path": str(path) if path else None},
        )

    def _post_dialog_studio(self) -> None:
        _ = self._read_json_body()
        path = choose_studio_dataset_dialog()
        self._send_json(
            {"ok": path is not None, "path": str(path) if path else None},
        )

    def _post_import_studio(self) -> None:
        payload = self._read_json_body()
        path_obj = payload.get("path")
        if not isinstance(path_obj, str) or not path_obj:
            msg = "JSON body must include a non-empty string path"
            raise ValueError(msg)
        asset_dir_obj = payload.get("assetDir")
        if asset_dir_obj is not None and not isinstance(asset_dir_obj, str):
            msg = "assetDir must be a string when provided"
            raise ValueError(msg)
        result = import_studio_dataset(
            Path(path_obj),
            store=self.store,
            asset_dir=Path(asset_dir_obj) if asset_dir_obj else None,
        )
        self._send_json(
            {
                "ok": True,
                "imported": result.imported,
                "totalAssets": result.total_assets,
                "sourceType": result.source_type,
                "sourceId": result.source_id,
                "dataset": str(result.data_json),
                "assetRoot": str(result.image_root),
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


def choose_folder_dialog() -> Path | None:
    """Open a native-ish folder dialog and return the selected path."""
    system = platform.system().lower()
    if system == "darwin":
        return run_osascript_path(
            'POSIX path of (choose folder with prompt "Choose photo directory")',
        )
    if system == "windows":
        script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Choose photo directory'
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  Write-Output $dialog.SelectedPath
}
"""
        return run_powershell_path(script)
    return run_tk_folder_dialog()


def choose_studio_dataset_dialog() -> Path | None:
    """Open a native-ish file dialog for Studio JSON datasets."""
    system = platform.system().lower()
    if system == "darwin":
        script = (
            'POSIX path of (choose file with prompt "Choose Constellation Studio dataset" '
            'of type {"json"})'
        )
        return run_osascript_path(script)
    if system == "windows":
        script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Choose Constellation Studio dataset'
$dialog.Filter = 'JSON files (*.json)|*.json|All files (*.*)|*.*'
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  Write-Output $dialog.FileName
}
"""
        return run_powershell_path(script)
    return run_tk_file_dialog()


def run_osascript_path(script: str) -> Path | None:
    """Run an AppleScript path chooser and return its POSIX path."""
    try:
        result = subprocess.run(  # noqa: S603
            ["osascript", "-e", script],  # noqa: S607
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return run_tk_folder_dialog()
    if result.returncode != 0:
        return None
    selected = result.stdout.strip()
    return Path(selected).expanduser().resolve() if selected else None


def run_powershell_path(script: str) -> Path | None:
    """Run a PowerShell path chooser and return its path."""
    executable = (
        "powershell.exe" if platform.system().lower() == "windows" else "pwsh"
    )
    try:
        result = subprocess.run(  # noqa: S603
            [executable, "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    selected = result.stdout.strip().splitlines()
    if not selected:
        return None
    return Path(selected[-1]).expanduser().resolve()


def run_tk_folder_dialog() -> Path | None:
    """Run a Tk folder dialog when platform dialogs are unavailable."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(title="Choose photo directory")
    finally:
        root.destroy()
    return Path(selected).expanduser().resolve() if selected else None


def run_tk_file_dialog() -> Path | None:
    """Run a Tk file dialog when platform dialogs are unavailable."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askopenfilename(
            title="Choose Constellation Studio dataset",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
    finally:
        root.destroy()
    return Path(selected).expanduser().resolve() if selected else None


def source_capabilities() -> dict[str, object]:
    """Return BYO source types surfaced by the onboarding UI."""
    return {
        "sources": [
            {
                "type": "folder",
                "label": "Photo directory",
                "enabled": True,
                "importEndpoint": "/api/import/folder",
                "description": (
                    "Import images recursively from a local directory, "
                    "camera dump, or exported photo folder."
                ),
            },
            {
                "type": "studioDataset",
                "label": "Constellation Studio dataset",
                "enabled": True,
                "importEndpoint": "/api/import/studio",
                "description": (
                    "Open a constellation.json or constellation.studio.json "
                    "set produced by Studio."
                ),
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
    embedding_provider: EmbeddingProvider | None = None,
    embedding_batch_size: int = 32,
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
    ConfiguredBackendRequestHandler.embedding_provider = embedding_provider
    ConfiguredBackendRequestHandler.embedding_batch_size = embedding_batch_size
    ConfiguredBackendRequestHandler.import_lock = threading.Lock()
    ConfiguredBackendRequestHandler.import_thread = None
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
    parser.add_argument(
        "--embedding-engine",
        default="none",
        help="Embedding engine for folder imports: none, openclip, or onnx.",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_MODEL,
        help=f"OpenCLIP model name (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--embedding-pretrained",
        default=DEFAULT_PRETRAINED,
        help=f"OpenCLIP pretrained tag (default: {DEFAULT_PRETRAINED}).",
    )
    parser.add_argument(
        "--embedding-device",
        default="auto",
        help="OpenCLIP device: auto, cpu, mps, cuda.",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=32,
        help="Images per embedding batch.",
    )
    parser.add_argument(
        "--onnx-model",
        type=Path,
        default=None,
        help="Path to ONNX image encoder for --embedding-engine=onnx.",
    )
    parser.add_argument(
        "--onnx-provider",
        default="auto",
        help="ONNX Runtime provider: auto, cpu, cuda, directml, coreml.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the local app in the default browser after startup.",
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
        embedding_engine=str(args.embedding_engine),
        embedding_model=str(args.embedding_model),
        embedding_pretrained=str(args.embedding_pretrained),
        embedding_device=str(args.embedding_device),
        embedding_batch_size=int(args.embedding_batch_size),
        onnx_model=cast("Path | None", args.onnx_model),
        onnx_provider=str(args.onnx_provider),
    )
    paths = default_indexing_paths(config.data_dir)
    viewer_dist = config.viewer_dist or find_default_viewer_dist(Path.cwd())
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    embedding_provider = create_embedding_provider(
        engine=config.embedding_engine,
        model=config.embedding_model,
        pretrained=config.embedding_pretrained,
        device=config.embedding_device,
        onnx_model=config.onnx_model,
        onnx_provider=config.onnx_provider,
    )
    if embedding_provider is not None:
        store.set_embedding_engine(embedding_provider.cache_namespace)
    else:
        store.set_embedding_engine("none")
    handler = make_handler(
        store=store,
        asset_root=paths.asset_root,
        viewer_dist=viewer_dist,
        embedding_provider=embedding_provider,
        embedding_batch_size=config.embedding_batch_size,
    )
    with QuietThreadingHTTPServer(
        (config.host, config.port), handler
    ) as httpd:
        url = server_url(httpd, config.host)
        print(f"Constellation backend listening at {url}", flush=True)
        print(f"SQLite: {paths.db_path}", flush=True)
        print(f"Assets: {paths.asset_root}", flush=True)
        if bool(args.open):
            webbrowser.open(url)
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
    embedding_provider: EmbeddingProvider | None = None,
    embedding_batch_size: int = 32,
) -> Generator[str]:
    """Start an ephemeral backend server for tests."""
    paths = default_indexing_paths(data_dir)
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    handler = make_handler(
        store=store,
        asset_root=paths.asset_root,
        viewer_dist=viewer_dist,
        embedding_provider=embedding_provider,
        embedding_batch_size=embedding_batch_size,
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
