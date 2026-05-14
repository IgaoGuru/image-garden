"""JSON schema helpers for Constellation data."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import NotRequired, TypedDict, cast


class ImageJson(TypedDict):
    """One image record in the viewer data contract."""

    id: str
    url: str
    embedding: NotRequired[list[float]]
    position: NotRequired[list[float]]
    thumbnailUrl: NotRequired[str]
    width: NotRequired[int]
    height: NotRequired[int]
    metadata: NotRequired[dict[str, str]]


class ConstellationJson(TypedDict):
    """Viewer data contract emitted by Studio."""

    images: list[ImageJson]


class StudioManifestJson(TypedDict):
    """Local-only sidecar data used by ``serve.py`` defaults."""

    imageRoot: str
    dataJson: str
    urlPrefix: str


@dataclass(frozen=True, slots=True)
class EmbeddedImage:
    """A generated embedding for one sanitized image."""

    id: str
    url: str
    embedding: tuple[float, ...]
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> ImageJson:
        """Return a JSON-serializable representation."""
        payload: ImageJson = {
            "id": self.id,
            "url": self.url,
            "embedding": list(self.embedding),
        }
        if self.thumbnail_url is not None:
            payload["thumbnailUrl"] = self.thumbnail_url
        if self.width is not None:
            payload["width"] = self.width
        if self.height is not None:
            payload["height"] = self.height
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


def constellation_json(images: list[EmbeddedImage]) -> ConstellationJson:
    """Build the top-level JSON document."""
    return {"images": [image.to_json() for image in images]}


def write_constellation_json(path: Path, images: list[EmbeddedImage]) -> None:
    """Write images in the shared viewer/studio JSON data contract."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = constellation_json(images)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def studio_manifest_path(data_path: Path) -> Path:
    """Return the conventional local sidecar path for a data JSON path."""
    if data_path.suffix:
        return data_path.with_name(f"{data_path.stem}.studio.json")
    return data_path.with_name(f"{data_path.name}.studio.json")


def write_studio_manifest(
    *,
    data_path: Path,
    image_root: Path,
    url_prefix: str,
) -> Path:
    """Write local-only metadata so ``serve.py`` can run without args."""
    resolved_data_path = data_path.expanduser().resolve()
    payload: StudioManifestJson = {
        "imageRoot": str(image_root.expanduser().resolve()),
        "dataJson": str(resolved_data_path),
        "urlPrefix": url_prefix,
    }
    manifest_path = studio_manifest_path(resolved_data_path)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def read_studio_manifest(data_path: Path) -> StudioManifestJson:
    """Read and validate local Studio sidecar metadata."""
    manifest_path = studio_manifest_path(data_path.expanduser().resolve())
    loaded: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        msg = f"not a Constellation Studio manifest: {manifest_path}"
        raise ValueError(msg)
    manifest = cast("Mapping[str, object]", loaded)
    image_root = manifest.get("imageRoot")
    data_json = manifest.get("dataJson")
    url_prefix = manifest.get("urlPrefix")
    if not isinstance(image_root, str):
        msg = f"invalid Constellation Studio manifest: {manifest_path}"
        raise ValueError(msg)
    if not isinstance(data_json, str):
        msg = f"invalid Constellation Studio manifest: {manifest_path}"
        raise ValueError(msg)
    if not isinstance(url_prefix, str):
        msg = f"invalid Constellation Studio manifest: {manifest_path}"
        raise ValueError(msg)
    return {
        "imageRoot": image_root,
        "dataJson": data_json,
        "urlPrefix": url_prefix,
    }


def read_constellation_json(path: Path) -> ConstellationJson:
    """Read and validate a constellation JSON file."""
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        msg = f"not a Constellation data file: {path}"
        raise ValueError(msg)

    loaded_mapping = cast("Mapping[str, object]", loaded)
    images_obj = loaded_mapping.get("images")
    if not isinstance(images_obj, list):
        msg = f"not a Constellation data file: {path}"
        raise ValueError(msg)

    image_objects = cast("list[object]", images_obj)
    return {
        "images": [parse_image_json(image, path) for image in image_objects]
    }


def parse_image_json(image_obj: object, path: Path) -> ImageJson:
    """Parse one image record from a constellation JSON file."""
    if not isinstance(image_obj, Mapping):
        msg = f"invalid image record in: {path}"
        raise ValueError(msg)
    image_mapping = cast("Mapping[str, object]", image_obj)
    id_obj = image_mapping.get("id")
    url_obj = image_mapping.get("url")
    if not isinstance(id_obj, str) or not isinstance(url_obj, str):
        msg = f"invalid image id/url in: {path}"
        raise ValueError(msg)

    image: ImageJson = {
        "id": id_obj,
        "url": url_obj,
    }
    embedding = optional_embedding(image_mapping.get("embedding"), path)
    position = optional_position(image_mapping.get("position"), path)
    if embedding is None and position is None:
        msg = f"image must include embedding or position in: {path}"
        raise ValueError(msg)
    if embedding is not None:
        image["embedding"] = embedding
    if position is not None:
        image["position"] = position
    apply_optional_image_fields(image_mapping, image, path)
    return image


def optional_embedding(
    embedding_obj: object,
    path: Path,
) -> list[float] | None:
    """Parse an optional embedding list."""
    if embedding_obj is None:
        return None
    return parse_embedding(embedding_obj, path)


def parse_embedding(embedding_obj: object, path: Path) -> list[float]:
    """Parse and validate an embedding list."""
    if not isinstance(embedding_obj, list):
        msg = f"invalid embedding in: {path}"
        raise ValueError(msg)
    embedding: list[float] = []
    for value in cast("list[object]", embedding_obj):
        if isinstance(value, bool) or not isinstance(value, int | float):
            msg = f"invalid embedding value in: {path}"
            raise ValueError(msg)
        embedding.append(float(value))
    return embedding


def optional_position(
    position_obj: object,
    path: Path,
) -> list[float] | None:
    """Parse an optional precomputed 3D position."""
    if position_obj is None:
        return None
    position = parse_embedding(position_obj, path)
    if len(position) != 3:
        msg = f"invalid position in: {path}"
        raise ValueError(msg)
    return position


def apply_optional_image_fields(
    source: Mapping[str, object],
    image: ImageJson,
    path: Path,
) -> None:
    """Apply optional viewer fields after validation."""
    thumbnail_url = optional_str(source, "thumbnailUrl")
    if thumbnail_url is not None:
        image["thumbnailUrl"] = thumbnail_url
    width = optional_int(source, "width")
    if width is not None:
        image["width"] = width
    height = optional_int(source, "height")
    if height is not None:
        image["height"] = height
    metadata = optional_metadata(source.get("metadata"), path)
    if metadata:
        image["metadata"] = metadata


def optional_metadata(metadata_obj: object, path: Path) -> dict[str, str]:
    """Parse optional string metadata."""
    if metadata_obj is None:
        return {}
    if not isinstance(metadata_obj, Mapping):
        msg = f"invalid metadata in: {path}"
        raise ValueError(msg)
    metadata: dict[str, str] = {}
    metadata_mapping = cast("Mapping[object, object]", metadata_obj)
    for key, value in metadata_mapping.items():
        if not isinstance(key, str) or not isinstance(value, str):
            msg = f"invalid metadata in: {path}"
            raise ValueError(msg)
        metadata[key] = value
    return metadata


def optional_str(source: Mapping[str, object], key: str) -> str | None:
    """Return an optional string field after validation."""
    value = source.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"invalid {key} field"
        raise ValueError(msg)
    return value


def optional_int(source: Mapping[str, object], key: str) -> int | None:
    """Return an optional integer field after validation."""
    value = source.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"invalid {key} field"
        raise ValueError(msg)
    return value
