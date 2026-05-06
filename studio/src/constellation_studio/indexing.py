"""Folder import and prototype indexing pipeline."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote, urlsplit

from constellation_studio.assets import (
    AssetOptions,
    SanitizedImage,
    normalize_url_prefix,
    sanitize_directory,
)
from constellation_studio.cache import EmbeddingCache
from constellation_studio.index_store import (
    IndexStore,
    StoredRuntimeAsset,
    Vec3,
)
from constellation_studio.layout import positions_from_embeddings
from constellation_studio.schema import (
    ImageJson,
    StudioManifestJson,
    read_constellation_json,
    read_studio_manifest,
    studio_manifest_path,
)
from constellation_studio.source_adapters import (
    FolderSourceAdapter,
    SourceAsset,
)

if TYPE_CHECKING:
    from constellation_studio.embedding_providers import EmbeddingProvider

Embedding = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Result of a folder import into the local index store."""

    imported: int
    total_assets: int
    source_type: str
    source_id: str
    folder: Path


@dataclass(frozen=True, slots=True)
class StudioImportResult:
    """Result of a Studio dataset import into the local index store."""

    imported: int
    total_assets: int
    source_type: str
    source_id: str
    data_json: Path
    image_root: Path


@dataclass(frozen=True, slots=True)
class StudioDatasetPaths:
    """Resolved file paths for a portable Studio dataset."""

    data_json: Path
    image_root: Path
    url_prefix: str


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


def import_folder(  # noqa: PLR0913
    folder: Path,
    *,
    store: IndexStore,
    asset_root: Path,
    skip_errors: bool = True,
    embedding_provider: EmbeddingProvider | None = None,
    batch_size: int = 64,
) -> ImportResult:
    """Import a folder through the source-adapter/index-store boundary.

    This keeps the existing sanitized-JPEG folder import path, but persists
    viewer-runtime records with backend-owned positions in SQLite instead of
    requiring embeddings in runtime JSON.
    """
    resolved_folder = folder.expanduser().resolve()
    adapter = FolderSourceAdapter(resolved_folder)
    if embedding_provider is None:
        msg = (
            "No embedding engine configured. Start Constellation with a real "
            "embedding engine, e.g. --embedding-engine openclip or a bundled "
            "ONNX model. Images were not added to the map."
        )
        raise ValueError(msg)
    source_assets = {asset.source_asset_id: asset for asset in adapter.scan()}

    store.set_index_state("importing")
    store.set_job_progress(
        phase="scanning",
        completed=0,
        total=0,
        message=f"Scanning {resolved_folder}",
    )
    sanitized = sanitize_directory(
        resolved_folder,
        options=AssetOptions(
            asset_root=asset_root,
            skip_errors=skip_errors,
        ),
    )
    positions = positions_for_sanitized_records(
        sanitized,
        asset_root=asset_root,
        provider=embedding_provider,
        batch_size=batch_size,
        store=store,
    )
    store.set_job_progress(
        phase="indexing",
        completed=0,
        total=len(sanitized),
        message="Writing runtime catalog",
    )
    for completed, record in enumerate(sanitized, start=1):
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
                position=positions[record.id],
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
        store.set_job_progress(
            phase="indexing",
            completed=completed,
            total=len(sanitized),
            message="Writing runtime catalog",
        )
    store.set_last_import_path(resolved_folder)
    store.set_job_progress(
        phase="ready",
        completed=len(sanitized),
        total=len(sanitized),
        message="Import complete",
    )
    store.set_index_state("idle")
    return ImportResult(
        imported=len(sanitized),
        total_assets=store.count_assets(),
        source_type=adapter.source_type,
        source_id=adapter.source_id,
        folder=resolved_folder,
    )


def positions_for_sanitized_records(
    records: Sequence[SanitizedImage],
    *,
    asset_root: Path,
    provider: EmbeddingProvider | None,
    batch_size: int,
    store: IndexStore,
) -> dict[str, Vec3]:
    """Return positions for sanitized records using embeddings when available."""
    typed_records = list(records)
    if provider is None:
        msg = "No embedding provider configured for folder layout."
        raise ValueError(msg)

    store.set_embedding_engine(provider.cache_namespace)
    store.set_job_progress(
        phase="embedding",
        completed=0,
        total=len(typed_records),
        message=f"Embedding with {provider.cache_namespace}",
    )
    cache = EmbeddingCache(asset_root, provider.cache_namespace)
    embeddings: dict[str, Embedding] = {}
    missing: list[SanitizedImage] = []
    for record in typed_records:
        cached = cache.get(record.id)
        if cached is None:
            missing.append(record)
        else:
            embeddings[record.id] = cached

    completed = len(embeddings)
    if completed:
        store.set_job_progress(
            phase="embedding",
            completed=completed,
            total=len(typed_records),
            message=f"Reused {completed} cached embeddings",
        )
    for batch in batched(missing, batch_size):
        vectors = provider.embed_images(
            [record.image_path for record in batch]
        )
        if len(vectors) != len(batch):
            msg = "embedding provider returned the wrong number of vectors"
            raise RuntimeError(msg)
        for record, vector in zip(batch, vectors, strict=True):
            cache.set(record.id, vector)
            embeddings[record.id] = vector
        completed += len(batch)
        store.set_job_progress(
            phase="embedding",
            completed=completed,
            total=len(typed_records),
            message=f"Embedding with {provider.cache_namespace}",
        )

    store.set_job_progress(
        phase="layout",
        completed=0,
        total=len(typed_records),
        message="Building 3D layout",
    )
    positions = positions_from_embeddings(embeddings)
    store.set_job_progress(
        phase="layout",
        completed=len(typed_records),
        total=len(typed_records),
        message="Built 3D layout",
    )
    return positions


def batched[T](items: Sequence[T], batch_size: int) -> list[Sequence[T]]:
    """Split a sequence into non-empty batches."""
    if batch_size < 1:
        msg = "batch_size must be >= 1"
        raise ValueError(msg)
    return [
        items[index : index + batch_size]
        for index in range(0, len(items), batch_size)
    ]


def import_studio_dataset(
    dataset_path: Path,
    *,
    store: IndexStore,
    asset_dir: Path | None = None,
) -> StudioImportResult:
    """Import a portable Constellation Studio dataset.

    The dataset can be either ``constellation.json`` or the sidecar
    ``constellation.studio.json``. Existing Studio assets are referenced in
    place; the backend normalizes the records into SQLite runtime assets.
    """
    paths = resolve_studio_dataset_paths(dataset_path, asset_dir=asset_dir)
    data = read_constellation_json(paths.data_json)
    source_id = studio_source_id(paths.data_json)

    image_positions = positions_for_studio_images(data["images"])

    store.set_index_state("importing")
    imported = 0
    for image in data["images"]:
        image_path = resolve_studio_asset_path(image["url"], paths)
        thumbnail_url = image.get("thumbnailUrl", image["url"])
        thumbnail_path = resolve_studio_asset_path(thumbnail_url, paths)
        metadata = studio_runtime_metadata(image, paths.data_json)
        media_type = metadata_media_type(metadata)
        creation_date = metadata_str(metadata, "creationDate")
        store.upsert_asset(
            StoredRuntimeAsset(
                id=image["id"],
                thumbnail_path=thumbnail_path,
                file_path=image_path,
                position=image_positions[image["id"]],
                width=image.get("width"),
                height=image.get("height"),
                metadata=metadata,
                source_type="studioDataset",
                source_id=source_id,
                source_asset_id=image["id"],
                stable_key=stable_key_for_file(image_path),
                creation_date=creation_date,
                media_type=media_type,
            ),
        )
        imported += 1

    store.set_last_import_path(paths.data_json)
    store.set_index_state("idle")
    return StudioImportResult(
        imported=imported,
        total_assets=store.count_assets(),
        source_type="studioDataset",
        source_id=source_id,
        data_json=paths.data_json,
        image_root=paths.image_root,
    )


def resolve_studio_dataset_paths(
    dataset_path: Path,
    *,
    asset_dir: Path | None = None,
) -> StudioDatasetPaths:
    """Resolve a Studio data JSON/manifest into data and asset roots."""
    resolved_path = dataset_path.expanduser().resolve()
    if not resolved_path.is_file():
        msg = f"Studio dataset path does not exist: {resolved_path}"
        raise FileNotFoundError(msg)

    if resolved_path.name.endswith(".studio.json"):
        manifest = read_manifest_file(resolved_path)
        data_json = Path(manifest["dataJson"]).expanduser().resolve()
        image_root = Path(manifest["imageRoot"]).expanduser().resolve()
        url_prefix = manifest["urlPrefix"]
    else:
        data_json = resolved_path
        manifest_path = studio_manifest_path(data_json)
        if manifest_path.is_file():
            manifest = read_studio_manifest(data_json)
            image_root = Path(manifest["imageRoot"]).expanduser().resolve()
            url_prefix = manifest["urlPrefix"]
        else:
            image_root = infer_studio_image_root(data_json, asset_dir)
            url_prefix = "/assets/"

    if asset_dir is not None:
        image_root = asset_dir.expanduser().resolve()
    if not data_json.is_file():
        msg = f"Studio data JSON does not exist: {data_json}"
        raise FileNotFoundError(msg)
    if not image_root.is_dir():
        msg = f"Studio asset directory does not exist: {image_root}"
        raise FileNotFoundError(msg)
    return StudioDatasetPaths(
        data_json=data_json,
        image_root=image_root,
        url_prefix=normalize_url_prefix(url_prefix),
    )


def read_manifest_file(path: Path) -> StudioManifestJson:
    """Read a concrete ``*.studio.json`` manifest path."""
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        msg = f"not a Constellation Studio manifest: {path}"
        raise ValueError(msg)
    manifest = cast("Mapping[str, object]", loaded)
    image_root = manifest.get("imageRoot")
    data_json = manifest.get("dataJson")
    url_prefix = manifest.get("urlPrefix")
    if not isinstance(image_root, str):
        msg = f"invalid Constellation Studio manifest: {path}"
        raise ValueError(msg)
    if not isinstance(data_json, str):
        msg = f"invalid Constellation Studio manifest: {path}"
        raise ValueError(msg)
    if not isinstance(url_prefix, str):
        msg = f"invalid Constellation Studio manifest: {path}"
        raise ValueError(msg)
    return {
        "imageRoot": image_root,
        "dataJson": data_json,
        "urlPrefix": url_prefix,
    }


def infer_studio_image_root(
    data_json: Path,
    asset_dir: Path | None,
) -> Path:
    """Infer a Studio asset directory when no sidecar is present."""
    if asset_dir is not None:
        return asset_dir.expanduser().resolve()
    for candidate in (
        data_json.parent / "constellation-assets",
        data_json.parent / "assets",
        data_json.parent,
    ):
        if candidate.is_dir():
            return candidate.resolve()
    return data_json.parent.resolve()


def resolve_studio_asset_path(url: str, paths: StudioDatasetPaths) -> Path:
    """Resolve a local URL from Studio JSON to an existing file path."""
    route = unquote(urlsplit(url).path)
    if not route:
        msg = "Studio asset URL is empty"
        raise ValueError(msg)
    if urlsplit(url).scheme in {"http", "https"}:
        msg = f"Studio asset URL must be local, got: {url}"
        raise ValueError(msg)

    prefix = normalize_url_prefix(paths.url_prefix)
    if route.startswith(prefix):
        candidate = resolve_below(paths.image_root, route.removeprefix(prefix))
    elif route.startswith("/"):
        candidate = resolve_below(paths.image_root, route.lstrip("/"))
    else:
        candidate = resolve_below(paths.data_json.parent, route)

    if candidate is None or not candidate.is_file():
        msg = (
            f"Studio asset not found for URL {url!r} under {paths.image_root}"
        )
        raise FileNotFoundError(msg)
    return candidate


def resolve_below(root: Path, relative: str) -> Path | None:
    """Resolve a path below root, rejecting traversal."""
    resolved_root = root.expanduser().resolve()
    parts = [part for part in relative.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return None
    candidate = resolved_root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(resolved_root):
        return None
    return candidate


def studio_source_id(data_json: Path) -> str:
    """Return a stable source id for a Studio dataset path."""
    digest = hashlib.sha256(str(data_json).encode("utf-8")).hexdigest()[:16]
    return f"studio:{digest}"


def stable_key_for_file(path: Path) -> str:
    """Return a stable-ish key for a local Studio asset file."""
    stat = path.stat()
    return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"


def positions_for_studio_images(
    images: Sequence[ImageJson],
) -> dict[str, Vec3]:
    """Return precomputed positions or UMAP positions from Studio embeddings."""
    image_list = list(images)
    if all("position" in image for image in image_list):
        positions: dict[str, Vec3] = {}
        for image in image_list:
            position = image.get("position")
            if position is None:  # pragma: no cover - narrowed by all() above
                msg = f"missing position for image {image['id']}"
                raise ValueError(msg)
            positions[image["id"]] = (position[0], position[1], position[2])
        return positions
    if all("embedding" in image for image in image_list):
        embeddings: dict[str, Embedding] = {}
        for image in image_list:
            embedding = image.get("embedding")
            if embedding is None:  # pragma: no cover - narrowed by all() above
                msg = f"missing embedding for image {image['id']}"
                raise ValueError(msg)
            embeddings[image["id"]] = tuple(embedding)
        return positions_from_embeddings(embeddings)
    msg = (
        "Studio dataset must provide positions for every image or embeddings "
        "for every image. Mixed positioned/embedding-only datasets are not "
        "supported."
    )
    raise ValueError(msg)


def studio_runtime_metadata(
    image: ImageJson,
    data_json: Path,
) -> dict[str, object]:
    """Build runtime metadata for a Studio dataset image."""
    metadata: dict[str, object] = dict(image.get("metadata", {}))
    metadata.setdefault("datasetPath", str(data_json))
    metadata.setdefault("mediaType", "image")
    return metadata


def metadata_str(metadata: Mapping[str, object], key: str) -> str | None:
    """Return a metadata string when present."""
    value = metadata.get(key)
    return value if isinstance(value, str) else None


def metadata_media_type(metadata: Mapping[str, object]) -> str:
    """Return a valid media type string from metadata."""
    value = metadata_str(metadata, "mediaType")
    return value if value is not None else "image"


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
