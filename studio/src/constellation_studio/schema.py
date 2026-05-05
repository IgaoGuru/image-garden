"""JSON schema helpers for Constellation data."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast


class ImageJson(TypedDict):
    """One image record in the viewer data contract."""

    id: str
    url: str
    embedding: list[float]


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
    """A generated embedding for one image."""

    id: str
    url: str
    embedding: tuple[float, ...]

    def to_json(self) -> ImageJson:
        """Return a JSON-serializable representation."""
        return {
            "id": self.id,
            "url": self.url,
            "embedding": list(self.embedding),
        }


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
    images: list[ImageJson] = []
    for image_obj in image_objects:
        if not isinstance(image_obj, Mapping):
            msg = f"invalid image record in: {path}"
            raise ValueError(msg)
        image_mapping = cast("Mapping[str, object]", image_obj)
        id_obj = image_mapping.get("id")
        url_obj = image_mapping.get("url")
        embedding_obj = image_mapping.get("embedding")
        if not isinstance(id_obj, str) or not isinstance(url_obj, str):
            msg = f"invalid image id/url in: {path}"
            raise ValueError(msg)
        if not isinstance(embedding_obj, list):
            msg = f"invalid embedding in: {path}"
            raise ValueError(msg)
        embedding_values = cast("list[object]", embedding_obj)
        embedding: list[float] = []
        for value in embedding_values:
            if isinstance(value, bool) or not isinstance(value, int | float):
                msg = f"invalid embedding value in: {path}"
                raise ValueError(msg)
            embedding.append(float(value))
        images.append({"id": id_obj, "url": url_obj, "embedding": embedding})

    return {"images": images}
