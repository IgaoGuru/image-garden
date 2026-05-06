"""Image ingestion and sanitized JPEG asset generation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import import_module
from io import BytesIO
from pathlib import Path
from typing import cast

from PIL import Image, ImageOps, UnidentifiedImageError

from constellation_studio.images import discover_image_files, quote_path

DEFAULT_ASSET_URL_PREFIX = "/assets/"
DEFAULT_MAX_IMAGE_SIZE = 2048
DEFAULT_THUMBNAIL_SIZE = 384
DEFAULT_JPEG_QUALITY = 90

WarnCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class AssetOptions:
    """Options for creating canonical JPEG assets."""

    asset_root: Path
    asset_url_prefix: str = DEFAULT_ASSET_URL_PREFIX
    max_image_size: int = DEFAULT_MAX_IMAGE_SIZE
    thumbnail_size: int = DEFAULT_THUMBNAIL_SIZE
    jpeg_quality: int = DEFAULT_JPEG_QUALITY
    skip_errors: bool = False
    warn: WarnCallback | None = None


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

    records: list[SanitizedImage] = []
    seen_ids: set[str] = set()
    skipped = 0
    for path in paths:
        try:
            record = sanitize_image(path, options=options)
        except (OSError, RuntimeError, UnidentifiedImageError) as exc:
            if not options.skip_errors:
                raise
            skipped += 1
            if options.warn is not None:
                options.warn(f"Skipping {path}: {exc}")
            continue
        if record.id in seen_ids:
            skipped += 1
            if options.warn is not None:
                options.warn(
                    f"Skipping duplicate image {path}; same sanitized hash as "
                    f"{record.id}",
                )
            continue
        seen_ids.add(record.id)
        records.append(record)

    if not records:
        msg = f"all {len(paths)} discovered images failed ingestion"
        raise RuntimeError(msg)
    if skipped and options.warn is not None:
        options.warn(f"Skipped {skipped} image(s) during ingestion.")
    return records


def sanitize_image(path: Path, *, options: AssetOptions) -> SanitizedImage:
    """Convert one source image into canonical and thumbnail JPEG assets."""
    if options.max_image_size < 1:
        msg = "max image size must be >= 1"
        raise ValueError(msg)
    if options.thumbnail_size < 1:
        msg = "thumbnail size must be >= 1"
        raise ValueError(msg)

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")

    canonical = resize_copy(image, options.max_image_size)
    canonical_bytes = encode_jpeg(
        canonical,
        quality=options.jpeg_quality,
    )
    image_id = hashlib.sha256(canonical_bytes).hexdigest()

    image_path = options.asset_root / "images" / f"{image_id}.jpg"
    thumbnail_path = options.asset_root / "thumbs" / f"{image_id}.jpg"
    if not image_path.is_file():
        image_path.write_bytes(canonical_bytes)
    if not thumbnail_path.is_file():
        thumbnail = resize_copy(canonical, options.thumbnail_size)
        thumbnail_path.write_bytes(
            encode_jpeg(thumbnail, quality=options.jpeg_quality),
        )

    return SanitizedImage(
        id=image_id,
        source_path=path.expanduser().resolve(),
        image_path=image_path,
        thumbnail_path=thumbnail_path,
        url=asset_url(options.asset_url_prefix, "images", image_id),
        thumbnail_url=asset_url(options.asset_url_prefix, "thumbs", image_id),
        width=canonical.width,
        height=canonical.height,
    )


def resize_copy(image: Image.Image, max_size: int) -> Image.Image:
    """Return a resized RGB copy constrained to max_size on the long edge."""
    copied = image.copy()
    copied.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return copied


def encode_jpeg(image: Image.Image, *, quality: int) -> bytes:
    """Encode a Pillow RGB image as deterministic-ish optimized JPEG bytes."""
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
    )
    return buffer.getvalue()


def asset_paths(records: Sequence[SanitizedImage]) -> list[Path]:
    """Return canonical image paths from sanitized records."""
    return [record.image_path for record in records]
