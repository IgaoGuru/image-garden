"""Download the default CLIP ONNX image encoder."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

DEFAULT_ONNX_REPO = "Xenova/clip-vit-base-patch32"
DEFAULT_ONNX_FILE = "onnx/vision_model.onnx"
DEFAULT_ONNX_URL = f"https://huggingface.co/{DEFAULT_ONNX_REPO}/resolve/main/{DEFAULT_ONNX_FILE}"
DEFAULT_ONNX_OUTPUT = Path("models/clip-image-encoder.onnx")


def build_parser() -> argparse.ArgumentParser:
    """Build parser for the ONNX model downloader."""
    parser = argparse.ArgumentParser(
        description="Download Constellation's default ONNX CLIP image encoder.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_ONNX_OUTPUT,
        help=f"Output ONNX file path (default: {DEFAULT_ONNX_OUTPUT}).",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_ONNX_URL,
        help="Model URL to download.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload even if output already exists.",
    )
    return parser


def download_onnx_model(
    output: Path = DEFAULT_ONNX_OUTPUT,
    *,
    url: str = DEFAULT_ONNX_URL,
    force: bool = False,
) -> Path:
    """Download an ONNX model to output, atomically."""
    resolved = output.expanduser().resolve()
    if resolved.is_file() and not force:
        return resolved

    resolved.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading ONNX model from {url}", flush=True)
    print(f"Destination: {resolved}", flush=True)

    parsed_url = urlparse(url)
    if parsed_url.scheme != "https":
        msg = f"ONNX download URL must use https: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": "Constellation/0"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        total_header = response.headers.get("content-length")
        total = int(total_header) if total_header is not None else None
        with NamedTemporaryFile(
            "wb",
            delete=False,
            dir=resolved.parent,
            suffix=".download",
        ) as temp_file:
            temp_path = Path(temp_file.name)
            copied = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                temp_file.write(chunk)
                copied += len(chunk)
                if total:
                    percent = (copied / total) * 100.0
                    print(f"\r{percent:5.1f}%", end="", flush=True)
            if total:
                print(flush=True)
    shutil.move(temp_path, resolved)
    metadata = {
        "source": url,
        "repo": DEFAULT_ONNX_REPO,
        "file": DEFAULT_ONNX_FILE,
    }
    resolved.with_suffix(resolved.suffix + ".json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    normalized_argv = list(argv if argv is not None else sys.argv[1:])
    if normalized_argv[:1] == ["--"]:
        normalized_argv = normalized_argv[1:]
    parser = build_parser()
    args = parser.parse_args(normalized_argv)
    try:
        output = download_onnx_model(
            Path(args.output),
            url=str(args.url),
            force=bool(args.force),
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"ONNX model ready: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
