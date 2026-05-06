"""Local HTTP server for Constellation Studio previews."""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import sys
import threading
import webbrowser
from collections.abc import Generator, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import ClassVar
from urllib.parse import quote, unquote, urlsplit

from constellation_studio.schema import (
    read_constellation_json,
    read_studio_manifest,
    studio_manifest_path,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_URL_PREFIX = "/images/"

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Constellation Studio</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    body { margin: 0; background: #050507; color: #f4f0e8; }
    header { padding: 16px 20px; border-bottom: 1px solid #2a2622; display: flex; gap: 16px; align-items: baseline; }
    h1 { font-size: 18px; margin: 0; }
    #status { color: #c0b7aa; font-size: 13px; }
    #viewer { width: 100vw; height: calc(100vh - 58px); }
    .fallback { padding: 20px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 14px; }
    .card { background: #12100e; border: 1px solid #302a24; border-radius: 12px; overflow: hidden; }
    .card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: #090807; }
    .card div { padding: 8px; font-size: 12px; color: #cfc5b8; overflow-wrap: anywhere; }
    code { color: #f6c177; }
  </style>
</head>
<body>
  <header>
    <h1>Constellation Studio</h1>
    <div id="status">Loading <code>/data.json</code>…</div>
  </header>
  <main id="viewer"></main>
  <script type="module">
    const status = document.querySelector('#status');
    const root = document.querySelector('#viewer');
    const data = await fetch('/data.json').then((response) => {
      if (!response.ok) throw new Error(`data.json HTTP ${response.status}`);
      return response.json();
    });

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
        try {
          return await import(url);
        } catch (_) {}
      }
      return null;
    }

    const viewer = await importViewer();
    if (viewer && typeof viewer.mount === 'function') {
      status.textContent = `Mounted @constellation/viewer with ${data.images.length} images.`;
      viewer.mount(root, data, { backgroundColor: 0x050507 });
    } else {
      status.textContent = `Viewer package not found; showing Studio fallback grid for ${data.images.length} images.`;
      root.className = 'fallback';
      root.innerHTML = '<p>Build/copy <code>@constellation/viewer</code> into <code>/viewer</code> to get the 3D fly-through. Studio serving and image URLs are working.</p><div class="grid"></div>';
      const grid = root.querySelector('.grid');
      for (const image of data.images) {
        const card = document.createElement('article');
        card.className = 'card';
        const img = document.createElement('img');
        img.loading = 'lazy';
        img.src = image.url;
        img.alt = image.id;
        const caption = document.createElement('div');
        const shape = Array.isArray(image.embedding) ? `${image.embedding.length}D` : 'positioned';
        caption.textContent = `${image.id} · ${shape}`;
        card.append(img, caption);
        grid.append(card);
      }
    }
  </script>
</body>
</html>
"""


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with reusable sockets for quick restarts."""

    allow_reuse_address: bool = True


class StudioRequestHandler(BaseHTTPRequestHandler):
    """Request handler configured by ``make_handler``."""

    image_root: ClassVar[Path]
    data_path: ClassVar[Path]
    image_url_prefix: ClassVar[str]
    viewer_dist: ClassVar[Path | None]

    def do_GET(self) -> None:
        """Serve a GET request."""
        self._serve(send_body=True)

    def do_HEAD(self) -> None:
        """Serve a HEAD request."""
        self._serve(send_body=False)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Write compact access logs to stderr."""
        print(f"{self.address_string()} - {format % args}", file=sys.stderr)

    def _serve(self, *, send_body: bool) -> None:
        route = urlsplit(self.path).path
        if route in {"/", "/index.html"}:
            self._send_bytes(
                INDEX_HTML.encode("utf-8"),
                "text/html; charset=utf-8",
                send_body=send_body,
            )
            return
        if route == "/data.json":
            self._send_file(self.data_path, send_body=send_body)
            return
        if route == "/viewer-entry.js" and self.viewer_dist is not None:
            entry = find_viewer_entry_file(self.viewer_dist)
            if entry is None:
                self.send_error(HTTPStatus.NOT_FOUND, "viewer entry not found")
                return
            module = viewer_entry_module(self.viewer_dist, entry)
            self._send_bytes(
                module.encode("utf-8"),
                "text/javascript; charset=utf-8",
                send_body=send_body,
            )
            return
        image_route = normalize_url_prefix(self.image_url_prefix)
        if route.startswith(image_route):
            image_path = resolve_route_path(
                self.image_root,
                route.removeprefix(image_route),
            )
            self._send_file_or_404(image_path, send_body=send_body)
            return
        if route.startswith("/viewer/") and self.viewer_dist is not None:
            viewer_path = resolve_route_path(
                self.viewer_dist,
                route.removeprefix("/viewer/"),
            )
            self._send_file_or_404(viewer_path, send_body=send_body)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def _send_file_or_404(self, path: Path | None, *, send_body: bool) -> None:
        if path is None or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        self._send_file(path, send_body=send_body)

    def _send_file(self, path: Path, *, send_body: bool) -> None:
        content_type = (
            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        data = path.read_bytes()
        self._send_bytes(data, content_type, send_body=send_body)

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        *,
        send_body: bool,
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(data)


def normalize_url_prefix(prefix: str) -> str:
    """Normalize an image URL prefix for routing."""
    stripped = prefix.strip()
    if not stripped:
        return DEFAULT_URL_PREFIX
    with_leading = stripped if stripped.startswith("/") else f"/{stripped}"
    return with_leading if with_leading.endswith("/") else f"{with_leading}/"


def resolve_route_path(root: Path, route_tail: str) -> Path | None:
    """Resolve an untrusted URL tail below *root*, rejecting traversal."""
    root = root.expanduser().resolve()
    decoded = unquote(route_tail)
    parts = [part for part in decoded.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return None
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate


def make_handler(
    *,
    image_root: Path,
    data_path: Path,
    image_url_prefix: str = DEFAULT_URL_PREFIX,
    viewer_dist: Path | None,
) -> type[StudioRequestHandler]:
    """Return a request handler class bound to local paths."""

    class ConfiguredStudioRequestHandler(StudioRequestHandler):
        pass

    ConfiguredStudioRequestHandler.image_root = (
        image_root.expanduser().resolve()
    )
    ConfiguredStudioRequestHandler.data_path = data_path.expanduser().resolve()
    ConfiguredStudioRequestHandler.image_url_prefix = normalize_url_prefix(
        image_url_prefix
    )
    ConfiguredStudioRequestHandler.viewer_dist = (
        viewer_dist.expanduser().resolve() if viewer_dist is not None else None
    )
    return ConfiguredStudioRequestHandler


def find_default_viewer_dist(start: Path) -> Path | None:
    """Find packages/viewer/dist from the repo or current directory if present."""
    candidates = [start, *start.parents]
    for base in candidates:
        dist = base / "packages" / "viewer" / "dist"
        if dist.is_dir():
            return dist
    return None


def find_viewer_entry_file(viewer_dist: Path) -> Path | None:
    """Return the most likely ESM entry file from a built viewer dist."""
    preferred_names = [
        "constellation-viewer.js",
        "constellation-viewer.es.js",
        "constellation-viewer.mjs",
        "viewer.js",
        "viewer.mjs",
        "index.js",
        "index.mjs",
    ]
    for name in preferred_names:
        candidate = viewer_dist / name
        if candidate.is_file():
            return candidate

    js_files = sorted(
        [
            path
            for pattern in ["*.mjs", "*.js"]
            for path in viewer_dist.glob(pattern)
            if path.is_file()
        ],
        key=lambda path: path.name,
    )
    return js_files[0] if js_files else None


def quote_viewer_relative_path(path: Path) -> str:
    """Quote a viewer-dist-relative path for use in a browser import."""
    return "/".join(quote(part) for part in path.as_posix().split("/"))


def viewer_entry_module(viewer_dist: Path, entry: Path) -> str:
    """Build a stable module that re-exports the detected viewer entry."""
    relative = entry.resolve().relative_to(viewer_dist.resolve())
    url = f"/viewer/{quote_viewer_relative_path(relative)}"
    return f"export * from {json.dumps(url)};\n"


def build_parser() -> argparse.ArgumentParser:
    """Build the server CLI parser."""
    parser = argparse.ArgumentParser(
        description="Serve images plus Constellation JSON for local preview.",
    )
    parser.add_argument(
        "paths",
        type=Path,
        nargs="*",
        help=(
            "Optional [image_dir] [data_json]. With no image_dir, serve.py "
            "uses the Studio manifest written by embed.py."
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host.")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Bind port; use 0 for an ephemeral port.",
    )
    parser.add_argument(
        "--url-prefix",
        default=None,
        help=(
            "Image URL prefix to serve (default: manifest value or /images/)."
        ),
    )
    parser.add_argument(
        "--viewer-dist",
        type=Path,
        default=None,
        help="Directory containing built @constellation/viewer JS files.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open a browser automatically.",
    )
    return parser


def resolve_cli_paths(
    paths: Sequence[Path],
    *,
    url_prefix: str | None = None,
) -> tuple[Path, Path, str]:
    """Resolve flexible CLI path forms to image dir, JSON, and URL prefix."""
    if len(paths) > 2:
        msg = "expected at most two positional paths: [image_dir] [data_json]"
        raise ValueError(msg)

    image_dir: Path | None
    data_json: Path
    if not paths:
        image_dir = None
        data_json = Path("constellation.json")
    elif len(paths) == 1 and paths[0].expanduser().is_dir():
        image_dir = paths[0]
        data_json = Path("constellation.json")
    elif len(paths) == 1:
        image_dir = None
        data_json = paths[0]
    else:
        image_dir = paths[0]
        data_json = paths[1]

    resolved_data_json = data_json.expanduser().resolve()
    manifest_path = studio_manifest_path(resolved_data_json)
    if image_dir is not None:
        prefix = url_prefix or DEFAULT_URL_PREFIX
        if url_prefix is None and manifest_path.is_file():
            prefix = read_studio_manifest(resolved_data_json)["urlPrefix"]
        return (
            image_dir.expanduser().resolve(),
            resolved_data_json,
            normalize_url_prefix(prefix),
        )

    if not manifest_path.is_file():
        msg = (
            f"image_dir was not provided and Studio manifest does not exist: "
            f"{manifest_path}. Run embed.py first or pass image_dir explicitly."
        )
        raise FileNotFoundError(msg)
    manifest = read_studio_manifest(resolved_data_json)
    manifest_data_json = Path(manifest["dataJson"]).expanduser().resolve()
    if manifest_data_json != resolved_data_json:
        msg = (
            f"Studio manifest {manifest_path} points at {manifest_data_json}, "
            f"not {resolved_data_json}"
        )
        raise ValueError(msg)
    return (
        Path(manifest["imageRoot"]).expanduser().resolve(),
        resolved_data_json,
        normalize_url_prefix(url_prefix or manifest["urlPrefix"]),
    )


def validate_inputs(image_dir: Path, data_json: Path) -> None:
    """Validate image and JSON inputs before serving forever."""
    if not image_dir.expanduser().is_dir():
        msg = f"image directory does not exist: {image_dir}"
        raise FileNotFoundError(msg)
    if not data_json.expanduser().is_file():
        msg = f"data JSON does not exist: {data_json}"
        raise FileNotFoundError(msg)
    read_constellation_json(data_json.expanduser())


def server_url(server: TCPServer, host: str) -> str:
    """Return the HTTP URL for a bound server."""
    port = int(server.server_address[1])
    return f"http://{host}:{port}/"


def run(args: argparse.Namespace) -> int:
    """Run the preview server until interrupted."""
    image_dir, data_json, image_url_prefix = resolve_cli_paths(
        args.paths,
        url_prefix=args.url_prefix,
    )
    validate_inputs(image_dir, data_json)

    viewer_dist_arg = args.viewer_dist
    viewer_dist = (
        Path(viewer_dist_arg).expanduser().resolve()
        if viewer_dist_arg is not None
        else find_default_viewer_dist(Path.cwd())
    )
    if viewer_dist is not None and not viewer_dist.is_dir():
        msg = f"viewer dist does not exist: {viewer_dist}"
        raise FileNotFoundError(msg)

    handler = make_handler(
        image_root=image_dir,
        data_path=data_json,
        image_url_prefix=image_url_prefix,
        viewer_dist=viewer_dist,
    )
    with QuietThreadingHTTPServer(
        (str(args.host), int(args.port)), handler
    ) as httpd:
        url = server_url(httpd, str(args.host))
        print(f"Serving {data_json} and {image_dir} at {url}")
        print(f"Serving images under {image_url_prefix}")
        if viewer_dist is not None:
            print(f"Serving viewer assets from {viewer_dist}")
        if not bool(args.no_open):
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
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


@contextlib.contextmanager
def run_test_server(
    *,
    image_root: Path,
    data_path: Path,
    image_url_prefix: str = DEFAULT_URL_PREFIX,
    viewer_dist: Path | None = None,
) -> Generator[str]:
    """Start an ephemeral Studio server for tests."""
    handler = make_handler(
        image_root=image_root,
        data_path=data_path,
        image_url_prefix=image_url_prefix,
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
