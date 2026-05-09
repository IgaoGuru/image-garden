#!/usr/bin/env python3
"""Benchmark Constellation ONNX embedding throughput."""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from constellation_studio.assets import AssetOptions, sanitize_directory
from constellation_studio.download_onnx import (
    DEFAULT_ONNX_MODEL_ID,
    DEFAULT_ONNX_OUTPUT,
    download_onnx_model,
)
from constellation_studio.embed import DEFAULT_BATCH_SIZE, batched
from constellation_studio.embedding_providers import OnnxClipEmbeddingProvider


def build_parser() -> argparse.ArgumentParser:
    """Build benchmark parser."""
    parser = argparse.ArgumentParser(
        description="Benchmark sanitize + ONNX image embedding throughput.",
    )
    parser.add_argument(
        "--image-root",
        type=Path,
        default=None,
        help="Existing image directory. Synthetic JPEGs are generated if omitted.",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=DEFAULT_ONNX_OUTPUT,
        help=f"ONNX model path (default: {DEFAULT_ONNX_OUTPUT}).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_ONNX_MODEL_ID,
        help=f"Model id to download with --download (default: {DEFAULT_ONNX_MODEL_ID}).",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download the selected model before benchmarking if missing.",
    )
    parser.add_argument(
        "--count",
        type=positive_int,
        default=64,
        help="Synthetic image count when --image-root is omitted.",
    )
    parser.add_argument(
        "--batch-size",
        type=positive_int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Embedding batch size (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(".constellation-benchmark"),
        help="Benchmark working directory.",
    )
    parser.add_argument(
        "--onnx-provider",
        default="auto",
        help="ONNX Runtime provider selector: auto, cpu, coreml, directml, cuda.",
    )
    return parser


def positive_int(value: str) -> int:
    """Parse a positive integer."""
    parsed = int(value)
    if parsed < 1:
        msg = "value must be >= 1"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def create_synthetic_images(root: Path, *, count: int) -> Path:
    """Create deterministic JPEGs for benchmark runs."""
    root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        path = root / f"sample-{index:05d}.jpg"
        if path.is_file():
            continue
        color = (
            (index * 53) % 256,
            (index * 97) % 256,
            (index * 193) % 256,
        )
        image = Image.new("RGB", (1024, 768), color)
        image.save(path, quality=90)
    return root


def run(args: argparse.Namespace) -> int:
    """Run benchmark and print timing summary."""
    work_dir = Path(args.work_dir).expanduser().resolve()
    model_path = Path(args.model_path).expanduser().resolve()
    if bool(args.download) and not model_path.is_file():
        model_path = download_onnx_model(model_path, model_id=str(args.model))
    if not model_path.is_file():
        raise FileNotFoundError(model_path)

    image_root = (
        Path(args.image_root).expanduser().resolve()
        if args.image_root is not None
        else create_synthetic_images(
            work_dir / "samples",
            count=int(args.count),
        )
    )
    asset_root = work_dir / "assets"

    sanitize_start = time.perf_counter()
    records = sanitize_directory(
        image_root,
        options=AssetOptions(asset_root=asset_root, skip_errors=True),
    )
    sanitize_seconds = time.perf_counter() - sanitize_start

    provider = OnnxClipEmbeddingProvider(
        model_path=model_path,
        provider=str(args.onnx_provider),
    )
    embed_start = time.perf_counter()
    embedded = 0
    for batch in batched(records, int(args.batch_size)):
        provider.embed_images([record.image_path for record in batch])
        embedded += len(batch)
    embed_seconds = time.perf_counter() - embed_start
    total_seconds = sanitize_seconds + embed_seconds

    print(f"model: {model_path}")
    print(f"provider: onnx/{args.onnx_provider}")
    print(f"images: {len(records)}")
    print(
        f"sanitize: {sanitize_seconds:.2f}s ({len(records) / sanitize_seconds:.2f} img/s)"
    )
    print(f"embed: {embed_seconds:.2f}s ({embedded / embed_seconds:.2f} img/s)")
    print(f"total: {total_seconds:.2f}s ({len(records) / total_seconds:.2f} img/s)")
    print(f"cache namespace: {provider.cache_namespace}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
