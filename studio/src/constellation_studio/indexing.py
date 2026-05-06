"""Folder import and prototype indexing pipeline."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

from constellation_studio.assets import AssetOptions, sanitize_directory
from constellation_studio.index_store import (
    IndexStore,
    StoredRuntimeAsset,
    Vec3,
)
from constellation_studio.source_adapters import (
    FolderSourceAdapter,
    SourceAsset,
)


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Result of a folder import into the local index store."""

    imported: int
    total_assets: int
    source_type: str
    source_id: str
    folder: Path


@dataclass(frozen=True, slots=True)
class IndexingPaths:
    """Filesystem paths used by the backend/indexer prototype."""

    data_dir: Path
    asset_root: Path
    db_path: Path


def default_indexing_paths(data_dir: Path) -> IndexingPaths:
    """Return the default backend data, asset, and SQLite paths."""
    resolved = data_dir.expanduser().resolve()
    return IndexingPaths(
        data_dir=resolved,
        asset_root=resolved / "assets",
        db_path=resolved / "constellation.sqlite",
    )


def import_folder(
    folder: Path,
    *,
    store: IndexStore,
    asset_root: Path,
    skip_errors: bool = True,
) -> ImportResult:
    """Import a folder through the source-adapter/index-store boundary.

    This keeps the existing sanitized-JPEG folder import path, but persists
    viewer-runtime records with backend-owned positions in SQLite instead of
    requiring embeddings in runtime JSON.
    """
    resolved_folder = folder.expanduser().resolve()
    adapter = FolderSourceAdapter(resolved_folder)
    source_assets = {asset.source_asset_id: asset for asset in adapter.scan()}

    store.set_index_state("importing")
    sanitized = sanitize_directory(
        resolved_folder,
        options=AssetOptions(
            asset_root=asset_root,
            skip_errors=skip_errors,
        ),
    )
    for record in sanitized:
        relative_id = record.source_path.relative_to(
            resolved_folder
        ).as_posix()
        source_asset = source_assets.get(relative_id)
        if source_asset is None:
            source_asset = SourceAsset(
                source_asset_id=relative_id,
                source_type=adapter.source_type,
                stable_key=relative_id,
                width=record.width,
                height=record.height,
                media_type="image",
                metadata={"sourcePath": str(record.source_path)},
            )
        store.upsert_asset(
            StoredRuntimeAsset(
                id=record.id,
                thumbnail_path=record.thumbnail_path,
                file_path=record.image_path,
                position=position_for_asset_id(record.id),
                width=record.width,
                height=record.height,
                metadata=runtime_metadata(source_asset, record.source_path),
                source_type=adapter.source_type,
                source_id=adapter.source_id,
                source_asset_id=source_asset.source_asset_id,
                stable_key=source_asset.stable_key,
                creation_date=source_asset.creation_date,
                media_type=source_asset.media_type,
            ),
        )
    store.set_last_import_path(resolved_folder)
    store.set_index_state("idle")
    return ImportResult(
        imported=len(sanitized),
        total_assets=store.count_assets(),
        source_type=adapter.source_type,
        source_id=adapter.source_id,
        folder=resolved_folder,
    )


def runtime_metadata(
    source_asset: SourceAsset,
    source_path: Path,
) -> dict[str, object]:
    """Build runtime metadata from a source asset."""
    metadata = dict(source_asset.metadata)
    metadata.setdefault("sourcePath", str(source_path))
    if source_asset.creation_date is not None:
        metadata.setdefault("creationDate", source_asset.creation_date)
    metadata.setdefault("mediaType", source_asset.media_type)
    return metadata


def position_for_asset_id(asset_id: str, *, radius: float = 120.0) -> Vec3:
    """Return a deterministic persisted prototype layout position.

    This is intentionally semantic-layout agnostic: embeddings remain backend
    data, and later indexers can replace these coordinates with UMAP/t-SNE/other
    precomputed layouts without changing the runtime API.
    """
    digest = hashlib.sha256(asset_id.encode("utf-8")).digest()
    theta_unit = unit_from_digest(digest[0:8])
    z_unit = unit_from_digest(digest[8:16])
    r_unit = unit_from_digest(digest[16:24])

    theta = 2.0 * math.pi * theta_unit
    z = (2.0 * z_unit) - 1.0
    xy = math.sqrt(max(0.0, 1.0 - (z * z)))
    shell_radius = radius * (0.35 + (0.65 * r_unit))
    return (
        shell_radius * xy * math.cos(theta),
        shell_radius * xy * math.sin(theta),
        shell_radius * z,
    )


def unit_from_digest(digest: bytes) -> float:
    """Map digest bytes to [0, 1)."""
    value = int.from_bytes(digest, byteorder="big", signed=False)
    return value / float(1 << (8 * len(digest)))
