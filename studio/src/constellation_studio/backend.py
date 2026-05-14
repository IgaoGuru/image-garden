"""Local backend API for Constellation desktop/runtime clients."""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import platform
import shutil
import sqlite3
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

from PIL import Image

from constellation_studio.embed import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MODEL,
    DEFAULT_PRETRAINED,
)
from constellation_studio.embedding_providers import (
    EmbeddingProvider,
    create_embedding_provider,
    preflight_embedding_provider,
)
from constellation_studio.index_store import IndexStatus, IndexStore
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
STUDIO_VERSION = "0.1.0"
STUDIO_API_VERSION = "0.1"

PLAYVIEW_MISSING_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Constellation</title></head>
<body style="background:#000;color:#f6f1e8;font-family:monospace;padding:24px">
  <h1>Constellation</h1>
  <p>Playview build missing. Run <code>pnpm --filter @image-garden/playview build</code>.</p>
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
    playview_dist: Path | None = None
    embedding_engine: str = "none"
    embedding_model: str = DEFAULT_MODEL
    embedding_pretrained: str = DEFAULT_PRETRAINED
    embedding_device: str = "auto"
    embedding_batch_size: int = DEFAULT_BATCH_SIZE
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
    playview_dist: ClassVar[Path | None]
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
        self._serve_read_request(send_body=True)

    def do_HEAD(self) -> None:
        """Serve a HEAD request."""
        self._serve_read_request(send_body=False)

    def _serve_read_request(self, *, send_body: bool) -> None:
        """Serve a read request without leaking handler tracebacks."""
        try:
            self._serve_get(send_body=send_body)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            self._send_read_error(exc, send_body=send_body)

    def _send_read_error(
        self,
        exc: Exception,
        *,
        send_body: bool,
    ) -> None:
        """Return a clean read-side failure response."""
        route = urlsplit(self.path).path
        if route.startswith("/api/"):
            self._send_json(
                {"ok": False, "error": str(exc)},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                send_body=send_body,
            )
            return
        self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))

    def do_POST(self) -> None:  # noqa: C901, PLR0911
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
            if route == "/api/data/clear":
                self._post_clear_data()
                return
            if route == "/api/system/open-data-dir":
                self._post_open_data_dir()
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
        except sqlite3.Error as exc:
            self._send_json(
                {"ok": False, "error": str(exc)},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
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

    def _serve_get(self, *, send_body: bool) -> None:  # noqa: C901, PLR0911, PLR0912
        split = urlsplit(self.path)
        route = split.path
        query = parse_qs(split.query)
        if route in {"/", "/index.html"}:
            self._send_playview_index(send_body=send_body)
            return
        if (
            route.startswith(("/assets/", "/audio/"))
            and self.playview_dist is not None
        ):
            path = resolve_below(
                self.playview_dist,
                route.removeprefix("/"),
            )
            self._send_path_or_404(path, send_body=send_body)
            return
        if route == "/api/status":
            self._send_status(send_body=send_body)
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
        if route == "/api/atlas/index.json":
            self._get_atlas_index(query, send_body=send_body)
            return
        if route.startswith("/api/atlas/pages/"):
            self._get_atlas_page(
                route.removeprefix("/api/atlas/pages/"),
                send_body=send_body,
            )
            return
        if route == "/api/texture-array/index.json":
            self._get_texture_array_index(query, send_body=send_body)
            return
        if route.startswith("/api/texture-array/pages/"):
            self._get_texture_array_page(
                route.removeprefix("/api/texture-array/pages/"),
                send_body=send_body,
            )
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

    def _send_status(self, *, send_body: bool = True) -> None:
        """Send status without letting transient store failures kill polling."""
        try:
            status = self.store.status()
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            status = {
                "state": "error",
                "paused": False,
                "totalAssets": 0,
                "importedAssets": 0,
                "dbPath": str(self.store.db_path),
                "assetRoot": str(self.store.asset_root),
                "jobPhase": "status-error",
                "jobMessage": str(exc),
            }
        self._send_json(studio_status(status), send_body=send_body)

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

    def _post_clear_data(self) -> None:
        _ = self._read_json_body()
        with self.import_lock:
            if (
                self.import_thread is not None
                and self.import_thread.is_alive()
            ):
                msg = "cannot clear data while an import job is running"
                raise RuntimeError(msg)
            self.store.clear_assets()
            if self.asset_root.exists():
                shutil.rmtree(self.asset_root)
            self.asset_root.mkdir(parents=True, exist_ok=True)
        self._send_json({"ok": True, "status": self.store.status()})

    def _post_open_data_dir(self) -> None:
        _ = self._read_json_body()
        data_dir = self.asset_root.parent
        data_dir.mkdir(parents=True, exist_ok=True)
        open_path_in_file_manager(data_dir)
        self._send_json({"ok": True, "path": str(data_dir)})

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

    def _get_atlas_index(
        self,
        query: Mapping[str, list[str]],
        *,
        send_body: bool,
    ) -> None:
        thumb_size = bounded_int(query, "thumbSize", default=128, upper=512)
        page_size = bounded_int(query, "pageSize", default=4096, upper=4096)
        index = build_thumbnail_atlas_index(
            self.store,
            self.asset_root,
            thumb_size=thumb_size,
            page_size=page_size,
        )
        self._send_json(index, send_body=send_body)

    def _get_atlas_page(self, raw_page: str, *, send_body: bool) -> None:
        page_name = unquote(raw_page)
        if not page_name.endswith(".jpg"):
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        page_stem = page_name.removesuffix(".jpg")
        if not page_stem.startswith("page-"):
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            page_index = int(page_stem.removeprefix("page-"))
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        path = atlas_page_path(self.asset_root, page_index=page_index)
        self._send_path_or_404(path, send_body=send_body)

    def _get_texture_array_index(
        self,
        query: Mapping[str, list[str]],
        *,
        send_body: bool,
    ) -> None:
        thumb_size = bounded_int(query, "thumbSize", default=256, upper=512)
        layers_per_page = bounded_int(
            query,
            "layersPerPage",
            default=256,
            upper=1024,
        )
        index = build_texture_array_index(
            self.store,
            self.asset_root,
            thumb_size=thumb_size,
            layers_per_page=layers_per_page,
        )
        self._send_json(index, send_body=send_body)

    def _get_texture_array_page(self, raw_page: str, *, send_body: bool) -> None:
        path = resolve_below(texture_array_root(self.asset_root), raw_page)
        if path is None or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        self._send_path_or_404(path, send_body=send_body)

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
        deterministic_path = generated_asset_path(
            self.asset_root,
            asset_id,
            thumbnail=thumbnail,
        )
        if deterministic_path is not None and deterministic_path.is_file():
            self._send_path_or_404(deterministic_path, send_body=send_body)
            return

        path = (
            self.store.asset_thumbnail_path(asset_id)
            if thumbnail
            else self.store.asset_file_path(asset_id)
        )
        self._send_path_or_404(path, send_body=send_body)

    def _send_playview_index(self, *, send_body: bool) -> None:
        if self.playview_dist is None:
            self._send_bytes(
                PLAYVIEW_MISSING_HTML.encode("utf-8"),
                "text/html; charset=utf-8",
                send_body=send_body,
            )
            return
        self._send_path_or_404(
            self.playview_dist / "index.html",
            send_body=send_body,
        )

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
        self._send_file(path, content_type, send_body=send_body)

    def _send_file(
        self,
        path: Path,
        content_type: str,
        *,
        send_body: bool,
    ) -> None:
        """Stream a local file without loading it all into memory."""
        size = path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        if not send_body:
            return
        with path.open("rb") as file:
            shutil.copyfileobj(file, self.wfile)

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


def generated_asset_path(
    asset_root: Path,
    asset_id: str,
    *,
    thumbnail: bool,
) -> Path | None:
    """Return deterministic generated asset path for folder-import hashes."""
    if len(asset_id) != 64 or any(
        char not in "0123456789abcdef" for char in asset_id
    ):
        return None
    directory = "thumbs" if thumbnail else "images"
    return asset_root / directory / f"{asset_id}.jpg"


def atlas_dir(asset_root: Path, *, thumb_size: int = 128, page_size: int = 4096) -> Path:
    """Return thumbnail atlas cache directory."""
    return asset_root / "atlas" / f"thumb{thumb_size}-page{page_size}"


def atlas_page_path(
    asset_root: Path,
    *,
    page_index: int,
    thumb_size: int = 128,
    page_size: int = 4096,
) -> Path:
    """Return one thumbnail atlas page path."""
    return atlas_dir(asset_root, thumb_size=thumb_size, page_size=page_size) / (
        f"page-{page_index}.jpg"
    )


def build_thumbnail_atlas_index(
    store: IndexStore,
    asset_root: Path,
    *,
    thumb_size: int,
    page_size: int,
) -> dict[str, object]:
    """Build/generate thumbnail atlas pages and return atlas metadata."""
    cols = max(1, page_size // thumb_size)
    rows = cols
    page_capacity = cols * rows
    total = store.count_assets()
    assets = store.list_assets(limit=total, offset=0) if total > 0 else []
    ordered_assets = sorted(assets, key=atlas_sort_key)
    page_count = (len(ordered_assets) + page_capacity - 1) // page_capacity
    output_dir = atlas_dir(
        asset_root,
        thumb_size=thumb_size,
        page_size=page_size,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    pages: list[dict[str, object]] = []
    for page_index in range(page_count):
        page_assets = ordered_assets[
            page_index * page_capacity : (page_index + 1) * page_capacity
        ]
        page_path = atlas_page_path(
            asset_root,
            page_index=page_index,
            thumb_size=thumb_size,
            page_size=page_size,
        )
        if not page_path.is_file():
            write_thumbnail_atlas_page(
                page_path,
                page_assets,
                asset_root=asset_root,
                thumb_size=thumb_size,
                page_size=page_size,
                cols=cols,
            )
        pages.append(
            {
                "index": page_index,
                "url": f"/api/atlas/pages/page-{page_index}.jpg",
                "width": page_size,
                "height": page_size,
            }
        )
        for cell_index, asset in enumerate(page_assets):
            col = cell_index % cols
            row = cell_index // cols
            u0, v0, u1, v1 = atlas_uv_rect(
                asset,
                col=col,
                row=row,
                thumb_size=thumb_size,
                page_size=page_size,
            )
            entries.append(
                {
                    "id": str(asset["id"]),
                    "page": page_index,
                    "u0": u0,
                    "v0": v0,
                    "u1": u1,
                    "v1": v1,
                }
            )
    return {
        "thumbSize": thumb_size,
        "pageSize": page_size,
        "cols": cols,
        "rows": rows,
        "pageCapacity": page_capacity,
        "total": len(ordered_assets),
        "pageCount": page_count,
        "pages": pages,
        "entries": entries,
    }


def atlas_uv_rect(
    asset: Mapping[str, object],
    *,
    col: int,
    row: int,
    thumb_size: int,
    page_size: int,
) -> tuple[float, float, float, float]:
    """Return tight UV rect around the actual thumbnail inside an atlas cell."""
    width = max(1, int(asset.get("width", thumb_size) or thumb_size))
    height = max(1, int(asset.get("height", thumb_size) or thumb_size))
    if width >= height:
        rendered_width = thumb_size
        rendered_height = max(1, round(thumb_size * height / width))
    else:
        rendered_height = thumb_size
        rendered_width = max(1, round(thumb_size * width / height))
    x = col * thumb_size + (thumb_size - rendered_width) / 2
    y = row * thumb_size + (thumb_size - rendered_height) / 2
    inset = 0.5
    return (
        (x + inset) / page_size,
        (y + inset) / page_size,
        (x + rendered_width - inset) / page_size,
        (y + rendered_height - inset) / page_size,
    )


def atlas_sort_key(asset: Mapping[str, object]) -> tuple[int, str]:
    """Sort assets spatially so nearby thumbnails tend to share pages."""
    position_obj = asset.get("position")
    if not isinstance(position_obj, (list, tuple)) or len(position_obj) != 3:
        return (0, str(asset.get("id", "")))
    coords = [float(value) for value in position_obj]
    # Layout coordinates are usually within a few hundred units; clamp to a
    # stable cube before Morton interleaving for locality-preserving pages.
    normalized = [max(0, min(1023, int((coord + 512.0) * 1023.0 / 1024.0))) for coord in coords]
    return (morton3(normalized[0], normalized[1], normalized[2]), str(asset.get("id", "")))


def morton3(x: int, y: int, z: int) -> int:
    """Return a 30-bit Morton code for three 10-bit coordinates."""
    code = 0
    for bit in range(10):
        code |= ((x >> bit) & 1) << (3 * bit)
        code |= ((y >> bit) & 1) << (3 * bit + 1)
        code |= ((z >> bit) & 1) << (3 * bit + 2)
    return code


def write_thumbnail_atlas_page(  # noqa: PLR0913
    page_path: Path,
    assets: Sequence[Mapping[str, object]],
    *,
    asset_root: Path,
    thumb_size: int,
    page_size: int,
    cols: int,
) -> None:
    """Write one square JPEG atlas page from generated thumbnails."""
    page = Image.new("RGB", (page_size, page_size), (0, 0, 0))
    for cell_index, asset in enumerate(assets):
        asset_id = str(asset["id"])
        source = generated_asset_path(asset_root, asset_id, thumbnail=True)
        if source is None or not source.is_file():
            continue
        with Image.open(source) as loaded:
            thumbnail = loaded.convert("RGB")
            thumbnail.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)
            col = cell_index % cols
            row = cell_index // cols
            x = col * thumb_size + (thumb_size - thumbnail.width) // 2
            y = row * thumb_size + (thumb_size - thumbnail.height) // 2
            page.paste(thumbnail, (x, y))
    temporary_path = page_path.with_suffix(".tmp.jpg")
    page.save(temporary_path, format="JPEG", quality=82, optimize=True)
    temporary_path.replace(page_path)


def texture_array_root(asset_root: Path) -> Path:
    """Return texture-array cache root."""
    return asset_root / "texture-array"


def texture_array_dir(
    asset_root: Path,
    *,
    thumb_size: int = 256,
    layers_per_page: int = 256,
) -> Path:
    """Return texture-array cache directory for a tier."""
    return texture_array_root(asset_root) / f"thumb{thumb_size}-layers{layers_per_page}"


def texture_array_page_path(
    asset_root: Path,
    *,
    page_index: int,
    thumb_size: int = 256,
    layers_per_page: int = 256,
) -> Path:
    """Return one texture-array source page path."""
    return texture_array_dir(
        asset_root,
        thumb_size=thumb_size,
        layers_per_page=layers_per_page,
    ) / f"page-{page_index}.jpg"


def build_texture_array_index(
    store: IndexStore,
    asset_root: Path,
    *,
    thumb_size: int,
    layers_per_page: int,
) -> dict[str, object]:
    """Build/generate source pages for client-side DataArrayTexture uploads."""
    layers_per_page = max(1, layers_per_page)
    cols = max(1, int(layers_per_page**0.5))
    while cols * cols < layers_per_page:
        cols += 1
    rows = (layers_per_page + cols - 1) // cols
    page_width = cols * thumb_size
    page_height = rows * thumb_size
    total = store.count_assets()
    assets = store.list_assets(limit=total, offset=0) if total > 0 else []
    ordered_assets = sorted(assets, key=atlas_sort_key)
    page_count = (len(ordered_assets) + layers_per_page - 1) // layers_per_page
    output_dir = texture_array_dir(
        asset_root,
        thumb_size=thumb_size,
        layers_per_page=layers_per_page,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    pages: list[dict[str, object]] = []
    for page_index in range(page_count):
        page_assets = ordered_assets[
            page_index * layers_per_page : (page_index + 1) * layers_per_page
        ]
        page_path = texture_array_page_path(
            asset_root,
            page_index=page_index,
            thumb_size=thumb_size,
            layers_per_page=layers_per_page,
        )
        if not page_path.is_file():
            write_texture_array_source_page(
                page_path,
                page_assets,
                asset_root=asset_root,
                thumb_size=thumb_size,
                page_width=page_width,
                page_height=page_height,
                cols=cols,
            )
        pages.append(
            {
                "index": page_index,
                "url": f"/api/texture-array/pages/thumb{thumb_size}-layers{layers_per_page}/page-{page_index}.jpg",
                "width": page_width,
                "height": page_height,
                "layers": len(page_assets),
            }
        )
        for layer, asset in enumerate(page_assets):
            entries.append(
                {
                    "id": str(asset["id"]),
                    "page": page_index,
                    "layer": layer,
                    "width": int(asset.get("width", thumb_size) or thumb_size),
                    "height": int(asset.get("height", thumb_size) or thumb_size),
                }
            )
    return {
        "format": "rgba8-grid-jpeg",
        "thumbSize": thumb_size,
        "layersPerPage": layers_per_page,
        "cols": cols,
        "rows": rows,
        "pageWidth": page_width,
        "pageHeight": page_height,
        "total": len(ordered_assets),
        "pageCount": page_count,
        "pages": pages,
        "entries": entries,
    }


def write_texture_array_source_page(  # noqa: PLR0913
    page_path: Path,
    assets: Sequence[Mapping[str, object]],
    *,
    asset_root: Path,
    thumb_size: int,
    page_width: int,
    page_height: int,
    cols: int,
) -> None:
    """Write one grid page that the browser slices into texture-array layers."""
    page = Image.new("RGB", (page_width, page_height), (0, 0, 0))
    for cell_index, asset in enumerate(assets):
        asset_id = str(asset["id"])
        source = generated_asset_path(asset_root, asset_id, thumbnail=True)
        if source is None or not source.is_file():
            continue
        with Image.open(source) as loaded:
            thumbnail = loaded.convert("RGB")
            thumbnail = cover_resize(thumbnail, thumb_size)
            col = cell_index % cols
            row = cell_index // cols
            page.paste(thumbnail, (col * thumb_size, row * thumb_size))
    temporary_path = page_path.with_suffix(".tmp.jpg")
    page.save(temporary_path, format="JPEG", quality=88, optimize=True)
    temporary_path.replace(page_path)


def cover_resize(image: Image.Image, size: int) -> Image.Image:
    """Return a square cover-cropped copy of an image."""
    source_width, source_height = image.size
    if source_width <= 0 or source_height <= 0:
        return Image.new("RGB", (size, size), (0, 0, 0))
    scale = max(size / source_width, size / source_height)
    resized = image.resize(
        (
            max(size, round(source_width * scale)),
            max(size, round(source_height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - size) // 2)
    top = max(0, (resized.height - size) // 2)
    return resized.crop((left, top, left + size, top + size))


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


def open_path_in_file_manager(path: Path) -> None:
    """Reveal a local directory in the platform file manager."""
    system = platform.system().lower()
    if system == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        return
    if system == "windows":
        subprocess.Popen(["explorer", str(path)])  # noqa: S603, S607
        return
    subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607


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


def studio_status(status: IndexStatus | Mapping[str, object]) -> dict[str, object]:
    """Attach explicit Studio compatibility metadata to a status payload."""
    return {
        **dict(status),
        "studioApiVersion": STUDIO_API_VERSION,
        "studioVersion": STUDIO_VERSION,
    }


def source_capabilities() -> dict[str, object]:
    """Return BYO source types surfaced by Playview."""
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


def find_default_playview_dist(start: Path) -> Path | None:
    """Find bundled or repo-built Playview dist."""
    for base in [start, *start.parents]:
        for relative in (Path("playview-dist"), Path("playview") / "dist"):
            candidate = base / relative
            if candidate.is_dir():
                return candidate
    return None


def find_viewer_entry_file(viewer_dist: Path) -> Path | None:
    """Return the likely ESM viewer entry file."""
    names = [
        "image-garden-viewer.js",
        "image-garden-viewer.es.js",
        "image-garden-viewer.mjs",
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


def make_handler(  # noqa: PLR0913
    *,
    store: IndexStore,
    asset_root: Path,
    viewer_dist: Path | None,
    playview_dist: Path | None,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_batch_size: int = DEFAULT_BATCH_SIZE,
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
    ConfiguredBackendRequestHandler.playview_dist = (
        playview_dist.expanduser().resolve()
        if playview_dist is not None
        else None
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
        default=Path(".image-garden-backend"),
        help="App data directory for SQLite and generated assets.",
    )
    parser.add_argument(
        "--viewer-dist",
        type=Path,
        default=None,
        help="Optional built @image-garden/viewer dist directory.",
    )
    parser.add_argument(
        "--playview-dist",
        type=Path,
        default=None,
        help="Optional built @image-garden/playview dist directory.",
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
        default=DEFAULT_BATCH_SIZE,
        help=f"Images per embedding batch (default: {DEFAULT_BATCH_SIZE}).",
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
    playview_dist_arg = cast("Path | None", args.playview_dist)
    config = BackendConfig(
        host=str(args.host),
        port=int(args.port),
        data_dir=Path(args.data_dir),
        viewer_dist=viewer_dist_arg,
        playview_dist=playview_dist_arg,
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
    playview_dist = config.playview_dist or find_default_playview_dist(
        Path.cwd()
    )
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    embedding_provider = create_embedding_provider(
        engine=config.embedding_engine,
        model=config.embedding_model,
        pretrained=config.embedding_pretrained,
        device=config.embedding_device,
        onnx_model=config.onnx_model,
        onnx_provider=config.onnx_provider,
    )
    preflight_embedding_provider(embedding_provider)
    if embedding_provider is not None:
        store.set_embedding_engine(embedding_provider.cache_namespace)
    else:
        store.set_embedding_engine("none")
    handler = make_handler(
        store=store,
        asset_root=paths.asset_root,
        viewer_dist=viewer_dist,
        playview_dist=playview_dist,
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
    playview_dist: Path | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_batch_size: int = DEFAULT_BATCH_SIZE,
) -> Generator[str]:
    """Start an ephemeral backend server for tests."""
    paths = default_indexing_paths(data_dir)
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    handler = make_handler(
        store=store,
        asset_root=paths.asset_root,
        viewer_dist=viewer_dist,
        playview_dist=playview_dist,
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
