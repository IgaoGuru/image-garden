"""Pluggable source adapter prototypes for local indexing."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from PIL import Image, UnidentifiedImageError

from constellation_studio.images import discover_image_files

MediaType = Literal["image", "video", "livePhoto", "unknown"]
SourceChangeType = Literal["created", "updated", "deleted"]


@dataclass(frozen=True, slots=True)
class ThumbnailRequest:
    """Thumbnail options requested from a source adapter."""

    max_size: int = 384


@dataclass(frozen=True, slots=True)
class SourceAsset:
    """Source-normalized media asset before local indexing."""

    source_asset_id: str
    source_type: str
    stable_key: str
    width: int | None = None
    height: int | None = None
    media_type: MediaType = "unknown"
    creation_date: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceChange:
    """A source-adapter change notification."""

    change_type: SourceChangeType
    source_asset_id: str


class SourceAdapter(Protocol):
    """Source adapter boundary used by the importer/indexer."""

    @property
    def source_type(self) -> str:
        """Return the adapter family, e.g. ``folder``."""
        raise NotImplementedError

    @property
    def source_id(self) -> str:
        """Return the adapter instance id."""
        raise NotImplementedError

    def scan(self) -> Iterable[SourceAsset]:
        """Yield source assets discovered by this adapter."""
        raise NotImplementedError

    def get_thumbnail(
        self,
        asset_id: str,
        options: ThumbnailRequest,
    ) -> bytes | str:
        """Return source thumbnail bytes or a local path/URL."""
        raise NotImplementedError

    def get_original(self, asset_id: str) -> bytes | str:
        """Return source original bytes or a local path/URL."""
        raise NotImplementedError

    def watch_changes(self) -> Iterable[SourceChange]:
        """Yield source changes when the adapter supports watching."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class FolderSourceAdapter:
    """Recursive folder-backed source adapter."""

    root: Path
    _source_id: str | None = None

    @property
    def source_type(self) -> str:
        """Return this adapter's source type."""
        return "folder"

    @property
    def source_id(self) -> str:
        """Return a stable id for this folder source."""
        if self._source_id is not None:
            return self._source_id
        resolved = str(self.root.expanduser().resolve())
        digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
        return f"folder:{digest}"

    def scan(self) -> Iterator[SourceAsset]:
        """Yield recursively discovered still-image assets."""
        resolved_root = self.root.expanduser().resolve()
        for path in discover_image_files(resolved_root):
            yield self._asset_for_path(path, resolved_root)

    def get_thumbnail(
        self,
        asset_id: str,
        options: ThumbnailRequest,
    ) -> str:
        """Return the source file path as a thumbnail prototype."""
        _ = options
        return str(self._path_for_asset_id(asset_id))

    def get_original(self, asset_id: str) -> str:
        """Return the source file path."""
        return str(self._path_for_asset_id(asset_id))

    def watch_changes(self) -> Iterable[SourceChange]:
        """Folder watching is intentionally deferred for the prototype."""
        return ()

    def _path_for_asset_id(self, asset_id: str) -> Path:
        root = self.root.expanduser().resolve()
        candidate = root.joinpath(*asset_id.split("/")).resolve()
        if not candidate.is_relative_to(root):
            msg = f"asset id escapes source root: {asset_id}"
            raise ValueError(msg)
        return candidate

    def _asset_for_path(self, path: Path, root: Path) -> SourceAsset:
        relative_id = path.relative_to(root).as_posix()
        stat = path.stat()
        width, height = image_dimensions(path)
        creation_date = datetime.fromtimestamp(
            stat.st_mtime,
            tz=UTC,
        ).isoformat()
        stable_key = f"{relative_id}:{stat.st_size}:{stat.st_mtime_ns}"
        return SourceAsset(
            source_asset_id=relative_id,
            source_type=self.source_type,
            stable_key=stable_key,
            width=width,
            height=height,
            media_type="image",
            creation_date=creation_date,
            metadata={"sourcePath": str(path)},
        )


def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    """Return image dimensions without failing adapter scans."""
    try:
        with Image.open(path) as image:
            return image.width, image.height
    except (OSError, UnidentifiedImageError):
        return None, None
