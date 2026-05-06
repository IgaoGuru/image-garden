"""Command line interface for generating CLIP embeddings."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from constellation_studio.assets import (
    DEFAULT_ASSET_URL_PREFIX,
    DEFAULT_JPEG_QUALITY,
    DEFAULT_MAX_IMAGE_SIZE,
    DEFAULT_THUMBNAIL_SIZE,
    AssetOptions,
    SanitizedImage,
    sanitize_directory,
)
from constellation_studio.cache import EmbeddingCache
from constellation_studio.embedding_providers import (
    EmbeddingProvider,
    create_embedding_provider,
)
from constellation_studio.schema import (
    EmbeddedImage,
    write_constellation_json,
    write_studio_manifest,
)

DEFAULT_MODEL = "ViT-B-32"
DEFAULT_PRETRAINED = "laion2b_s34b_b79k"
DEFAULT_BATCH_SIZE = 64
DEFAULT_OUTPUT = Path("constellation.json")

Embedding = tuple[float, ...]
ProgressCallback = Callable[[int, int], None]
WarnCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class EmbedOptions:
    """Options for embedding a directory."""

    asset_root: Path | None = None
    asset_url_prefix: str = DEFAULT_ASSET_URL_PREFIX
    max_image_size: int = DEFAULT_MAX_IMAGE_SIZE
    thumbnail_size: int = DEFAULT_THUMBNAIL_SIZE
    jpeg_quality: int = DEFAULT_JPEG_QUALITY
    batch_size: int = DEFAULT_BATCH_SIZE
    progress: ProgressCallback | None = None
    skip_errors: bool = False
    warn: WarnCallback | None = None
    cache_namespace: str = "default"
    use_cache: bool = True


@dataclass(frozen=True, slots=True)
class EmbedContext:
    """Shared dependencies for embedding uncached records."""

    embedder: EmbeddingProvider
    cache: EmbeddingCache
    options: EmbedOptions
    completed_offset: int
    total: int


def batched[T](paths: Sequence[T], batch_size: int) -> list[Sequence[T]]:
    """Split a sequence into non-empty batches."""
    if batch_size < 1:
        msg = "batch size must be >= 1"
        raise ValueError(msg)
    return [
        paths[index : index + batch_size]
        for index in range(0, len(paths), batch_size)
    ]


def embed_batch_individually(
    records: Sequence[SanitizedImage],
    *,
    embedder: EmbeddingProvider,
    warn: Callable[[str], None] | None = None,
) -> list[tuple[SanitizedImage, Embedding]]:
    """Embed records one at a time, returning only successful records."""
    embedded: list[tuple[SanitizedImage, Embedding]] = []
    for record in records:
        try:
            embeddings = embedder.embed_images([record.image_path])
        except RuntimeError as exc:
            if warn is not None:
                warn(f"Skipping {record.source_path}: {exc}")
            continue
        if len(embeddings) != 1:
            if warn is not None:
                warn(
                    f"Skipping {record.source_path}: backend returned "
                    f"{len(embeddings)} vectors",
                )
            continue
        embedded.append((record, embeddings[0]))
    return embedded


def embed_batch(
    records: Sequence[SanitizedImage],
    *,
    embedder: EmbeddingProvider,
    skip_errors: bool,
    warn: WarnCallback | None,
) -> list[tuple[SanitizedImage, Embedding]]:
    """Embed one batch, optionally retrying one-by-one on failure."""
    try:
        embeddings = embedder.embed_images(
            [record.image_path for record in records]
        )
    except RuntimeError:
        if not skip_errors:
            raise
        return embed_batch_individually(records, embedder=embedder, warn=warn)

    if len(embeddings) == len(records):
        return list(zip(records, embeddings, strict=True))
    if skip_errors:
        return embed_batch_individually(records, embedder=embedder, warn=warn)

    msg = "embedding backend returned the wrong number of vectors"
    raise RuntimeError(msg)


def embed_directory(
    image_root: Path,
    *,
    embedder: EmbeddingProvider,
    options: EmbedOptions | None = None,
) -> list[EmbeddedImage]:
    """Ingest *image_root*, embed sanitized JPEGs, and return records."""
    root = image_root.expanduser().resolve()
    opts = options or EmbedOptions()
    asset_root = (
        opts.asset_root.expanduser().resolve()
        if opts.asset_root is not None
        else (Path.cwd() / "constellation-assets").resolve()
    )
    sanitized = sanitize_directory(
        root,
        options=AssetOptions(
            asset_root=asset_root,
            asset_url_prefix=opts.asset_url_prefix,
            max_image_size=opts.max_image_size,
            thumbnail_size=opts.thumbnail_size,
            jpeg_quality=opts.jpeg_quality,
            skip_errors=opts.skip_errors,
            warn=opts.warn,
        ),
    )

    cache = EmbeddingCache(asset_root, opts.cache_namespace)
    cached, to_embed = cached_embeddings(
        sanitized,
        cache=cache,
        use_cache=opts.use_cache,
        progress=opts.progress,
    )
    embedded, skipped = embed_uncached(
        to_embed,
        context=EmbedContext(
            embedder=embedder,
            cache=cache,
            options=opts,
            completed_offset=len(cached),
            total=len(sanitized),
        ),
    )
    all_embedded = [*cached, *embedded]

    if not all_embedded:
        msg = f"all {len(sanitized)} sanitized images failed to embed"
        raise RuntimeError(msg)
    if cached and opts.warn is not None:
        opts.warn(f"Reused {len(cached)} cached embedding(s).")
    if skipped and opts.warn is not None:
        opts.warn(f"Skipped {skipped} image(s) that failed to embed.")

    all_embedded.sort(key=lambda image: image.id)
    return all_embedded


def cached_embeddings(
    records: Sequence[SanitizedImage],
    *,
    cache: EmbeddingCache,
    use_cache: bool,
    progress: ProgressCallback | None,
) -> tuple[list[EmbeddedImage], list[SanitizedImage]]:
    """Split records into cached embedded images and records still needed."""
    embedded: list[EmbeddedImage] = []
    missing: list[SanitizedImage] = []
    for record in records:
        cached = cache.get(record.id) if use_cache else None
        if cached is None:
            missing.append(record)
            continue
        embedded.append(embedded_image(record, cached))
        if progress is not None:
            progress(len(embedded), len(records))
    return embedded, missing


def embed_uncached(
    records: Sequence[SanitizedImage],
    *,
    context: EmbedContext,
) -> tuple[list[EmbeddedImage], int]:
    """Embed uncached records and return viewer images plus skip count."""
    embedded: list[EmbeddedImage] = []
    skipped = 0
    completed = context.completed_offset
    options = context.options
    for batch in batched(records, options.batch_size):
        batch_records = embed_batch(
            batch,
            embedder=context.embedder,
            skip_errors=options.skip_errors,
            warn=options.warn,
        )
        skipped += len(batch) - len(batch_records)
        batch_by_id = {
            record.id: embedding for record, embedding in batch_records
        }
        for record in batch:
            embedding = batch_by_id.get(record.id)
            if embedding is None:
                continue
            if options.use_cache:
                context.cache.set(record.id, embedding)
            embedded.append(embedded_image(record, embedding))
        completed += len(batch)
        if options.progress is not None:
            options.progress(completed, context.total)
    return embedded, skipped


def embedded_image(
    record: SanitizedImage, embedding: Embedding
) -> EmbeddedImage:
    """Build a viewer image record from a sanitized image and embedding."""
    return EmbeddedImage(
        id=record.id,
        url=record.url,
        thumbnail_url=record.thumbnail_url,
        embedding=embedding,
        width=record.width,
        height=record.height,
        metadata={"sourcePath": str(record.source_path)},
    )


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
        "--asset-dir",
        type=Path,
        default=None,
        help=(
            "Directory for sanitized JPEGs, thumbnails, and cache "
            "(default: <output-stem>-assets next to output)."
        ),
    )
    parser.add_argument(
        "--url-prefix",
        default=DEFAULT_ASSET_URL_PREFIX,
        help="URL prefix used by serve.py for generated assets (default: /assets/).",
    )
    parser.add_argument(
        "--max-image-size",
        type=positive_int,
        default=DEFAULT_MAX_IMAGE_SIZE,
        help=f"Canonical JPEG long edge in pixels (default: {DEFAULT_MAX_IMAGE_SIZE}).",
    )
    parser.add_argument(
        "--thumbnail-size",
        type=positive_int,
        default=DEFAULT_THUMBNAIL_SIZE,
        help=f"Thumbnail JPEG long edge in pixels (default: {DEFAULT_THUMBNAIL_SIZE}).",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=positive_int,
        default=DEFAULT_JPEG_QUALITY,
        help=f"JPEG quality for generated assets (default: {DEFAULT_JPEG_QUALITY}).",
    )
    parser.add_argument(
        "--embedding-engine",
        choices=["openclip", "onnx"],
        default="openclip",
        help="Embedding engine to use (default: openclip).",
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
        "--onnx-model",
        type=Path,
        default=None,
        help="Path to an ONNX image encoder when using --embedding-engine=onnx.",
    )
    parser.add_argument(
        "--onnx-provider",
        default="auto",
        help="ONNX Runtime provider: auto, cpu, cuda, directml, coreml.",
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
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Recompute embeddings instead of reading/writing the embedding cache.",
    )
    return parser


def default_asset_root(output: Path) -> Path:
    """Return the default asset root for an output JSON path."""
    resolved = output.expanduser().resolve()
    stem = resolved.stem if resolved.suffix else resolved.name
    return resolved.with_name(f"{stem}-assets")


def run(args: argparse.Namespace) -> int:
    """Run the embedding CLI from parsed args."""
    image_dir = Path(args.image_dir)
    output = Path(args.output)
    asset_root = (
        Path(args.asset_dir).expanduser().resolve()
        if args.asset_dir is not None
        else default_asset_root(output)
    )
    device = str(args.device)
    model = str(args.model)
    pretrained = str(args.pretrained)
    engine = str(args.embedding_engine)
    embedder = create_embedding_provider(
        engine=engine,
        model=model,
        pretrained=pretrained,
        device=device,
        onnx_model=args.onnx_model,
        onnx_provider=str(args.onnx_provider),
    )
    if embedder is None:
        msg = "constellation-embed requires an embedding engine"
        raise ValueError(msg)
    cache_namespace = embedder.cache_namespace

    print(f"Loading embedding engine {cache_namespace}...")

    print(f"Ingesting images under {image_dir.expanduser().resolve()}...")
    print(f"Writing sanitized assets under {asset_root}...")

    images = embed_directory(
        image_dir,
        embedder=embedder,
        options=EmbedOptions(
            asset_root=asset_root,
            asset_url_prefix=str(args.url_prefix),
            max_image_size=int(args.max_image_size),
            thumbnail_size=int(args.thumbnail_size),
            jpeg_quality=int(args.jpeg_quality),
            batch_size=int(args.batch_size),
            progress=lambda completed, total: print(
                f"Embedded {completed}/{total} images..."
            ),
            skip_errors=bool(args.skip_errors),
            warn=lambda message: print(f"warning: {message}", file=sys.stderr),
            cache_namespace=cache_namespace,
            use_cache=not bool(args.no_cache),
        ),
    )
    write_constellation_json(output, images)
    manifest_path = write_studio_manifest(
        data_path=output,
        image_root=asset_root,
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
