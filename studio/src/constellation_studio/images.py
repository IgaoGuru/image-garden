"""Image discovery and URL helpers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".avif",
        ".bmp",
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }
)


def discover_image_files(
    root: Path,
    *,
    extensions: Iterable[str] = IMAGE_EXTENSIONS,
) -> list[Path]:
    """Return supported image files under *root* in deterministic order."""
    root = root.expanduser().resolve()
    normalized_extensions = {ext.lower() for ext in extensions}

    if not root.exists():
        msg = f"image path does not exist: {root}"
        raise FileNotFoundError(msg)
    if not root.is_dir():
        msg = f"image path is not a directory: {root}"
        raise NotADirectoryError(msg)

    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in normalized_extensions
    ]
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def image_id(path: Path, root: Path) -> str:
    """Return a stable POSIX relative id for *path*."""
    return (
        path.expanduser()
        .resolve()
        .relative_to(root.expanduser().resolve())
        .as_posix()
    )


def quote_path(path: str) -> str:
    """Quote each POSIX path segment while preserving separators."""
    return "/".join(quote(segment) for segment in path.split("/"))


def image_url(path: Path, root: Path, url_prefix: str) -> str:
    """Build the browser URL for an image served below *url_prefix*."""
    prefix = url_prefix if url_prefix.endswith("/") else f"{url_prefix}/"
    return f"{prefix}{quote_path(image_id(path, root))}"
