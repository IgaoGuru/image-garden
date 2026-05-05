"""Command line interface for generating CLIP embeddings."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from constellation_studio.images import (
    discover_image_files,
    image_id,
    image_url,
)
from constellation_studio.open_clip_backend import (
    OpenClipImageEmbedder,
    default_device,
)
from constellation_studio.schema import (
    EmbeddedImage,
    write_constellation_json,
    write_studio_manifest,
)

DEFAULT_MODEL = "ViT-B-32"
DEFAULT_PRETRAINED = "laion2b_s34b_b79k"
DEFAULT_BATCH_SIZE = 32
DEFAULT_OUTPUT = Path("constellation.json")
DEFAULT_URL_PREFIX = "/images/"

Embedding = tuple[float, ...]
ProgressCallback = Callable[[int, int], None]
WarnCallback = Callable[[str], None]


class ImageEmbedder(Protocol):
    """Minimal embedding backend interface."""

    def embed_images(self, paths: Sequence[Path]) -> list[Embedding]:
        """Embed image paths in order."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class EmbedOptions:
    """Options for embedding a directory."""

    url_prefix: str = DEFAULT_URL_PREFIX
    batch_size: int = DEFAULT_BATCH_SIZE
    progress: ProgressCallback | None = None
    skip_errors: bool = False
    warn: WarnCallback | None = None


def batched(paths: Sequence[Path], batch_size: int) -> list[Sequence[Path]]:
    """Split a path sequence into non-empty batches."""
    if batch_size < 1:
        msg = "batch size must be >= 1"
        raise ValueError(msg)
    return [
        paths[index : index + batch_size]
        for index in range(0, len(paths), batch_size)
    ]


def embed_batch_individually(
    paths: Sequence[Path],
    *,
    embedder: ImageEmbedder,
    warn: Callable[[str], None] | None = None,
) -> list[tuple[Path, Embedding]]:
    """Embed paths one at a time, returning only successful records."""
    records: list[tuple[Path, Embedding]] = []
    for path in paths:
        try:
            embeddings = embedder.embed_images([path])
        except RuntimeError as exc:
            if warn is not None:
                warn(f"Skipping {path}: {exc}")
            continue
        if len(embeddings) != 1:
            if warn is not None:
                warn(
                    f"Skipping {path}: backend returned {len(embeddings)} vectors"
                )
            continue
        records.append((path, embeddings[0]))
    return records


def embed_batch(
    paths: Sequence[Path],
    *,
    embedder: ImageEmbedder,
    skip_errors: bool,
    warn: WarnCallback | None,
) -> list[tuple[Path, Embedding]]:
    """Embed one batch, optionally retrying one-by-one on failure."""
    try:
        embeddings = embedder.embed_images(paths)
    except RuntimeError:
        if not skip_errors:
            raise
        return embed_batch_individually(paths, embedder=embedder, warn=warn)

    if len(embeddings) == len(paths):
        return list(zip(paths, embeddings, strict=True))
    if skip_errors:
        return embed_batch_individually(paths, embedder=embedder, warn=warn)

    msg = "embedding backend returned the wrong number of vectors"
    raise RuntimeError(msg)


def embed_directory(
    image_root: Path,
    *,
    embedder: ImageEmbedder,
    options: EmbedOptions | None = None,
) -> list[EmbeddedImage]:
    """Walk *image_root*, embed images, and return viewer-ready records."""
    root = image_root.expanduser().resolve()
    paths = discover_image_files(root)
    if not paths:
        msg = f"no supported images found under {root}"
        raise ValueError(msg)

    opts = options or EmbedOptions()
    total = len(paths)
    completed = 0
    embedded: list[EmbeddedImage] = []
    skipped = 0
    for batch in batched(paths, opts.batch_size):
        records = embed_batch(
            batch,
            embedder=embedder,
            skip_errors=opts.skip_errors,
            warn=opts.warn,
        )
        skipped += len(batch) - len(records)
        for path, embedding in records:
            embedded.append(
                EmbeddedImage(
                    id=image_id(path, root),
                    url=image_url(path, root, opts.url_prefix),
                    embedding=embedding,
                )
            )
        completed += len(batch)
        if opts.progress is not None:
            opts.progress(completed, total)
    if not embedded:
        msg = f"all {total} discovered images failed to embed"
        raise RuntimeError(msg)
    if skipped and opts.warn is not None:
        opts.warn(f"Skipped {skipped} image(s) that failed to embed.")
    return embedded


def positive_int(value: str) -> int:
    """Argparse type for positive integers."""
    parsed = int(value)
    if parsed < 1:
        msg = "must be >= 1"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Generate Constellation JSON from a folder of images.",
    )
    parser.add_argument(
        "image_dir",
        type=Path,
        help="Directory containing images to embed.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output JSON path (default: constellation.json).",
    )
    parser.add_argument(
        "--url-prefix",
        default=DEFAULT_URL_PREFIX,
        help="URL prefix used by serve.py for image URLs (default: /images/).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"open_clip model name (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--pretrained",
        default=DEFAULT_PRETRAINED,
        help=f"open_clip pretrained tag (default: {DEFAULT_PRETRAINED}).",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="Torch device: auto, cpu, mps, or cuda (default: auto).",
    )
    parser.add_argument(
        "--batch-size",
        type=positive_int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Images per inference batch (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--skip-errors",
        action="store_true",
        help=(
            "Skip unreadable images after retrying failed batches one-by-one."
        ),
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Run the embedding CLI from parsed args."""
    image_dir = Path(args.image_dir)
    output = Path(args.output)
    device_arg = str(args.device)
    device = default_device() if device_arg == "auto" else device_arg

    print(
        f"Loading CLIP model {args.model!s}/{args.pretrained!s} on {device}..."
    )
    embedder = OpenClipImageEmbedder(
        model=str(args.model),
        pretrained=str(args.pretrained),
        device=device,
    )

    print(f"Embedding images under {image_dir.expanduser().resolve()}...")
    images = embed_directory(
        image_dir,
        embedder=embedder,
        options=EmbedOptions(
            url_prefix=str(args.url_prefix),
            batch_size=int(args.batch_size),
            progress=lambda completed, total: print(
                f"Embedded {completed}/{total} images..."
            ),
            skip_errors=bool(args.skip_errors),
            warn=lambda message: print(f"warning: {message}", file=sys.stderr),
        ),
    )
    write_constellation_json(output, images)
    manifest_path = write_studio_manifest(
        data_path=output,
        image_root=image_dir,
        url_prefix=str(args.url_prefix),
    )
    embedding_dim = len(images[0].embedding) if images else 0
    print(
        f"Wrote {len(images)} images with {embedding_dim}D embeddings to "
        f"{output.expanduser().resolve()}"
    )
    print(f"Wrote Studio manifest to {manifest_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
