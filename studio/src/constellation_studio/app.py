"""Consumer-friendly local app launcher."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from constellation_studio import backend
from constellation_studio.download_onnx import download_onnx_model
from constellation_studio.embed import DEFAULT_MODEL, DEFAULT_PRETRAINED


def default_app_data_dir() -> Path:
    """Return the default per-user Constellation app data directory."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return Path(root) / "Constellation"
    if sys.platform == "darwin":
        return (
            Path.home() / "Library" / "Application Support" / "Constellation"
        )
    root = os.environ.get("XDG_DATA_HOME") or str(
        Path.home() / ".local" / "share"
    )
    return Path(root) / "constellation"


def find_bundled_onnx_model(start: Path) -> Path | None:
    """Find a bundled ONNX image encoder model if one is present."""
    env_model = os.environ.get("CONSTELLATION_ONNX_MODEL")
    if env_model:
        candidate = Path(env_model).expanduser().resolve()
        if candidate.is_file():
            return candidate
    names = (
        "clip-image-encoder.onnx",
        "openclip-image-encoder.onnx",
        "model.onnx",
    )
    for base in [start, *start.parents]:
        for directory in (base / "models", base):
            for name in names:
                candidate = directory / name
                if candidate.is_file():
                    return candidate.resolve()
    return None


def find_bundled_viewer_dist(start: Path) -> Path | None:
    """Find a bundled or repo-built viewer dist."""
    candidates = [start, *start.parents]
    for base in candidates:
        for relative in (
            Path("viewer-dist"),
            Path("packages") / "viewer" / "dist",
        ):
            candidate = base / relative
            if candidate.is_dir():
                return candidate.resolve()
    return None


def build_parser() -> argparse.ArgumentParser:
    """Build the local app launcher parser."""
    parser = argparse.ArgumentParser(
        description="Start the local Constellation app and open a browser.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=default_app_data_dir(),
        help="Local app data directory.",
    )
    parser.add_argument(
        "--viewer-dist",
        type=Path,
        default=None,
        help="Built @constellation/viewer dist directory.",
    )
    parser.add_argument(
        "--host", default=backend.DEFAULT_HOST, help="Bind host."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Bind port; default uses an ephemeral port.",
    )
    parser.add_argument(
        "--embedding-engine",
        default="auto",
        help="Embedding engine: auto, none, openclip, or onnx.",
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
        "--no-open",
        action="store_true",
        help="Do not open the browser automatically.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Run the browser-first local app."""
    viewer_dist = cast("Path | None", args.viewer_dist)
    if viewer_dist is None:
        env_viewer = os.environ.get("CONSTELLATION_VIEWER_DIST")
        viewer_dist = (
            Path(env_viewer)
            if env_viewer
            else find_bundled_viewer_dist(Path.cwd())
        )
    if viewer_dist is None:
        print(
            "warning: viewer dist not found; backend will show its fallback UI. "
            "Run `pnpm --filter @constellation/viewer build` in development.",
        )

    engine = str(args.embedding_engine).strip().lower()
    data_dir = Path(args.data_dir).expanduser().resolve()
    onnx_model = cast("Path | None", args.onnx_model)
    search_start = (
        viewer_dist.parent if viewer_dist is not None else Path.cwd()
    )
    if onnx_model is None:
        onnx_model = find_bundled_onnx_model(search_start)
    if engine == "auto":
        if onnx_model is None:
            onnx_model = download_onnx_model(
                data_dir / "models" / "clip-image-encoder.onnx",
            )
        engine = "onnx"
    elif engine == "onnx" and onnx_model is None:
        print(
            "warning: --embedding-engine onnx selected but no ONNX model was "
            "found; startup will fail unless --onnx-model is provided.",
        )

    backend_args = argparse.Namespace(
        host=str(args.host),
        port=int(args.port),
        data_dir=data_dir,
        viewer_dist=viewer_dist,
        embedding_engine=engine,
        embedding_model=str(args.embedding_model),
        embedding_pretrained=str(args.embedding_pretrained),
        embedding_device=str(args.embedding_device),
        embedding_batch_size=int(args.embedding_batch_size),
        onnx_model=onnx_model,
        onnx_provider=str(args.onnx_provider),
        open=not bool(args.no_open),
    )
    print("✦ Constellation")
    print(f"Data directory: {data_dir}")
    print(f"Embedding engine: {engine}")
    return backend.run(backend_args)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    normalized_argv = list(argv if argv is not None else sys.argv[1:])
    if normalized_argv[:1] == ["--"]:
        normalized_argv = normalized_argv[1:]
    parser = build_parser()
    args = parser.parse_args(normalized_argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
