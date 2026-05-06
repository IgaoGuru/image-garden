"""Local backend API for Constellation desktop/runtime clients."""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import platform
import shutil
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

PLAYVIEW_MISSING_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Constellation</title></head>
<body style="background:#000;color:#f6f1e8;font-family:monospace;padding:24px">
  <h1>Constellation</h1>
  <p>Playview build missing. Run <code>pnpm --filter @constellation/playview build</code>.</p>
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
        self._serve_get(send_body=True)

    def do_HEAD(self) -> None:
        """Serve a HEAD request."""
        self._serve_get(send_body=False)

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
            self._send_playview_index(send_body=send_body)
            return
        if route.startswith("/assets/") and self.playview_dist is not None:
            path = resolve_below(
                self.playview_dist,
                route.removeprefix("/"),
            )
            self._send_path_or_404(path, send_body=send_body)
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


def make_handler(  # noqa: PLR0913
    *,
    store: IndexStore,
    asset_root: Path,
    viewer_dist: Path | None,
    playview_dist: Path | None,
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
        "--playview-dist",
        type=Path,
        default=None,
        help="Optional built @constellation/playview dist directory.",
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
        default=8,
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
    embedding_batch_size: int = 32,
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
