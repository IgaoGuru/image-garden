"""Image ingestion and sanitized JPEG asset generation."""

from __future__ import annotations

import hashlib
import os
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from importlib import import_module
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from PIL import Image, ImageOps, UnidentifiedImageError

from constellation_studio.images import discover_image_files, quote_path

DEFAULT_ASSET_URL_PREFIX = "/assets/"
DEFAULT_MAX_IMAGE_SIZE = 2048
DEFAULT_THUMBNAIL_SIZE = 384
DEFAULT_JPEG_QUALITY = 90
DEFAULT_JPEG_OPTIMIZE = False
DEFAULT_JPEG_PROGRESSIVE = False

WarnCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class AssetOptions:
    """Options for creating canonical JPEG assets."""

    asset_root: Path
    asset_url_prefix: str = DEFAULT_ASSET_URL_PREFIX
    max_image_size: int = DEFAULT_MAX_IMAGE_SIZE
    thumbnail_size: int = DEFAULT_THUMBNAIL_SIZE
    jpeg_quality: int = DEFAULT_JPEG_QUALITY
    jpeg_optimize: bool = DEFAULT_JPEG_OPTIMIZE
    jpeg_progressive: bool = DEFAULT_JPEG_PROGRESSIVE
    skip_errors: bool = False
    warn: WarnCallback | None = None
    progress: ProgressCallback | None = None
    sanitize_workers: int | None = None


@dataclass(frozen=True, slots=True)
class SanitizedImage:
    """One source image converted into backend-owned JPEG assets."""

    id: str
    source_path: Path
    image_path: Path
    thumbnail_path: Path
    url: str
    thumbnail_url: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class PreparedSanitizedImage:
    """One sanitized image plus encoded bytes awaiting serial writes."""

    record: SanitizedImage
    image_bytes: bytes
    thumbnail_bytes: bytes


def register_heif_opener() -> None:
    """Register Pillow HEIF/HEIC support when pillow-heif is installed."""
    try:
        module = import_module("pillow_heif")
    except ImportError:
        return
    register = cast(
        "Callable[[], None]", module.__dict__["register_heif_opener"]
    )
    register()


def normalize_url_prefix(prefix: str) -> str:
    """Normalize an asset URL prefix."""
    stripped = prefix.strip()
    if not stripped:
        return DEFAULT_ASSET_URL_PREFIX
    with_leading = stripped if stripped.startswith("/") else f"/{stripped}"
    return with_leading if with_leading.endswith("/") else f"{with_leading}/"


def asset_url(asset_url_prefix: str, kind: str, image_id: str) -> str:
    """Build a browser URL for a generated asset."""
    prefix = normalize_url_prefix(asset_url_prefix)
    return f"{prefix}{quote_path(kind)}/{quote_path(image_id)}.jpg"


def sanitize_directory(
    image_root: Path,
    *,
    options: AssetOptions,
) -> list[SanitizedImage]:
    """Convert discovered source images into canonical JPEG assets."""
    register_heif_opener()
    paths = discover_image_files(image_root)
    if not paths:
        msg = f"no supported images found under {image_root.expanduser().resolve()}"
        raise ValueError(msg)

    options.asset_root.mkdir(parents=True, exist_ok=True)
    (options.asset_root / "images").mkdir(exist_ok=True)
    (options.asset_root / "thumbs").mkdir(exist_ok=True)

    worker_count = sanitize_worker_count(options, len(paths))
    records, skipped = collect_sanitized_records(
        prepare_sanitized_images(
            paths,
            options=options,
            worker_count=worker_count,
        ),
        options=options,
        total=len(paths),
    )

    if not records:
        msg = f"all {len(paths)} discovered images failed ingestion"
        raise RuntimeError(msg)
    if skipped and options.warn is not None:
        options.warn(f"Skipped {skipped} image(s) during ingestion.")
    return records


def collect_sanitized_records(
    prepared_images: Iterator[
        tuple[Path, PreparedSanitizedImage | None, Exception | None]
    ],
    *,
    options: AssetOptions,
    total: int,
) -> tuple[list[SanitizedImage], int]:
    """Write prepared images, skip failures/duplicates, return records."""
    records: list[SanitizedImage] = []
    seen_ids: set[str] = set()
    skipped = 0
    processed = 0
    for path, prepared, error in prepared_images:
        processed += 1
        if error is not None:
            skipped += handle_sanitize_error(path, error, options=options)
            report_progress(options, processed, total)
            continue
        if prepared is None:  # pragma: no cover - defensive
            msg = f"failed to sanitize image without an error: {path}"
            raise RuntimeError(msg)
        if prepared.record.id in seen_ids:
            skipped += 1
            warn_duplicate(path, prepared.record, options=options)
            report_progress(options, processed, total)
            continue
        write_prepared_sanitized_image(prepared)
        seen_ids.add(prepared.record.id)
        records.append(prepared.record)
        report_progress(options, processed, total)
    return records, skipped


def report_progress(options: AssetOptions, completed: int, total: int) -> None:
    """Report sanitize progress when a callback is configured."""
    if options.progress is not None:
        options.progress(completed, total)


def handle_sanitize_error(
    path: Path,
    error: Exception,
    *,
    options: AssetOptions,
) -> int:
    """Handle one sanitize error according to skip policy."""
    if not options.skip_errors:
        raise error
    if options.warn is not None:
        options.warn(f"Skipping {path}: {error}")
    return 1


def warn_duplicate(
    path: Path,
    record: SanitizedImage,
    *,
    options: AssetOptions,
) -> None:
    """Warn about one duplicate image when warnings are enabled."""
    if options.warn is not None:
        message = f"Skipping duplicate image {path}; same sanitized hash as {record.id}"
        options.warn(message)


def sanitize_image(path: Path, *, options: AssetOptions) -> SanitizedImage:
    """Convert one source image into canonical and thumbnail JPEG assets."""
    prepared = prepare_sanitized_image(path, options=options)
    write_prepared_sanitized_image(prepared)
    return prepared.record


def prepare_sanitized_images(
    paths: Sequence[Path],
    *,
    options: AssetOptions,
    worker_count: int,
) -> Iterator[tuple[Path, PreparedSanitizedImage | None, Exception | None]]:
    """Prepare sanitized images, preserving input order and errors."""
    if worker_count <= 1:
        for path in paths:
            yield prepare_sanitized_image_result(path, options)
        return

    def prepare(
        path: Path,
    ) -> tuple[
        Path,
        PreparedSanitizedImage | None,
        Exception | None,
    ]:
        return prepare_sanitized_image_result(path, options)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        yield from bounded_ordered_map(
            executor,
            prepare,
            paths,
            max_pending=worker_count * 2,
        )


def bounded_ordered_map[T, R](
    executor: ThreadPoolExecutor,
    function: Callable[[T], R],
    items: Sequence[T],
    *,
    max_pending: int,
) -> Iterator[R]:
    """Map with bounded pending futures while preserving input order."""
    item_iter = iter(items)
    pending: deque[Future[R]] = deque()

    def submit_next() -> bool:
        try:
            item = next(item_iter)
        except StopIteration:
            return False
        pending.append(executor.submit(function, item))
        return True

    for _ in range(max(1, max_pending)):
        if not submit_next():
            break

    while pending:
        future = pending.popleft()
        yield future.result()
        _ = submit_next()


def prepare_sanitized_image_result(
    path: Path,
    options: AssetOptions,
) -> tuple[Path, PreparedSanitizedImage | None, Exception | None]:
    """Return one prepared image or its ingestion error."""
    try:
        return path, prepare_sanitized_image(path, options=options), None
    except (OSError, RuntimeError, UnidentifiedImageError) as exc:
        return path, None, exc


def prepare_sanitized_image(
    path: Path,
    *,
    options: AssetOptions,
) -> PreparedSanitizedImage:
    """Decode, normalize, resize, encode, and hash one source image."""
    if options.max_image_size < 1:
        msg = "max image size must be >= 1"
        raise ValueError(msg)
    if options.thumbnail_size < 1:
        msg = "thumbnail size must be >= 1"
        raise ValueError(msg)

    with Image.open(path) as source:
        _ = source.draft(
            "RGB", (options.max_image_size, options.max_image_size)
        )
        image = ImageOps.exif_transpose(source).convert("RGB")

    canonical = resize_copy(image, options.max_image_size)
    canonical_bytes = encode_jpeg(
        canonical,
        quality=options.jpeg_quality,
        optimize=options.jpeg_optimize,
        progressive=options.jpeg_progressive,
    )
    image_id = hashlib.sha256(canonical_bytes).hexdigest()
    thumbnail = resize_copy(canonical, options.thumbnail_size)
    thumbnail_bytes = encode_jpeg(
        thumbnail,
        quality=options.jpeg_quality,
        optimize=options.jpeg_optimize,
        progressive=options.jpeg_progressive,
    )

    image_path = options.asset_root / "images" / f"{image_id}.jpg"
    thumbnail_path = options.asset_root / "thumbs" / f"{image_id}.jpg"
    return PreparedSanitizedImage(
        record=SanitizedImage(
            id=image_id,
            source_path=path.expanduser().resolve(),
            image_path=image_path,
            thumbnail_path=thumbnail_path,
            url=asset_url(options.asset_url_prefix, "images", image_id),
            thumbnail_url=asset_url(
                options.asset_url_prefix,
                "thumbs",
                image_id,
            ),
            width=canonical.width,
            height=canonical.height,
        ),
        image_bytes=canonical_bytes,
        thumbnail_bytes=thumbnail_bytes,
    )


def write_prepared_sanitized_image(prepared: PreparedSanitizedImage) -> None:
    """Write prepared canonical and thumbnail JPEGs if missing."""
    record = prepared.record
    write_bytes_if_missing(record.image_path, prepared.image_bytes)
    write_bytes_if_missing(record.thumbnail_path, prepared.thumbnail_bytes)


def write_bytes_if_missing(path: Path, content: bytes) -> None:
    """Atomically write content unless the destination already exists."""
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        _ = temp_file.write(content)
    try:
        _ = temp_path.replace(path)
    except OSError:
        if not path.is_file():
            raise
        temp_path.unlink(missing_ok=True)


def sanitize_worker_count(options: AssetOptions, image_count: int) -> int:
    """Return worker count for CPU-heavy image sanitization."""
    if image_count <= 1:
        return 1
    if options.sanitize_workers is not None:
        if options.sanitize_workers < 1:
            msg = "sanitize_workers must be >= 1"
            raise ValueError(msg)
        return min(options.sanitize_workers, image_count)
    cpu_count = os.process_cpu_count() or os.cpu_count() or 1
    return min(max(1, cpu_count), image_count)


def resize_copy(image: Image.Image, max_size: int) -> Image.Image:
    """Return a resized RGB copy constrained to max_size on the long edge."""
    copied = image.copy()
    copied.thumbnail((max_size, max_size), Image.Resampling.BICUBIC)
    return copied


def encode_jpeg(
    image: Image.Image,
    *,
    quality: int,
    optimize: bool,
    progressive: bool,
) -> bytes:
    """Encode a Pillow RGB image with import-tuned JPEG settings."""
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=optimize,
        progressive=progressive,
    )
    return buffer.getvalue()


def asset_paths(records: Sequence[SanitizedImage]) -> list[Path]:
    """Return canonical image paths from sanitized records."""
    return [record.image_path for record in records]
