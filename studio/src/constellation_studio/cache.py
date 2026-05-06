"""Small JSON-file embedding cache keyed by sanitized image hash."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast

Embedding = tuple[float, ...]


class EmbeddingCacheJson(TypedDict):
    """On-disk embedding cache record."""

    id: str
    namespace: str
    embedding: list[float]


@dataclass(frozen=True, slots=True)
class EmbeddingCache:
    """Embedding cache stored as one JSON file per image id."""

    root: Path
    namespace: str

    @property
    def directory(self) -> Path:
        """Return the namespace-specific cache directory."""
        return (
            self.root
            / "cache"
            / "embeddings"
            / safe_namespace(
                self.namespace,
            )
        )

    def get(self, image_id: str) -> Embedding | None:
        """Return a cached embedding for image_id, if valid."""
        path = self.path_for(image_id)
        if not path.is_file():
            return None
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return None
        data = cast("dict[str, object]", loaded)
        if (
            data.get("id") != image_id
            or data.get("namespace") != self.namespace
        ):
            return None
        embedding_obj = data.get("embedding")
        if not isinstance(embedding_obj, list):
            return None
        embedding_values = cast("list[object]", embedding_obj)
        embedding: list[float] = []
        for value in embedding_values:
            if isinstance(value, bool) or not isinstance(value, int | float):
                return None
            embedding.append(float(value))
        return tuple(embedding)

    def set(self, image_id: str, embedding: Embedding) -> None:
        """Store an embedding for image_id."""
        self.directory.mkdir(parents=True, exist_ok=True)
        payload: EmbeddingCacheJson = {
            "id": image_id,
            "namespace": self.namespace,
            "embedding": list(embedding),
        }
        self.path_for(image_id).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )

    def path_for(self, image_id: str) -> Path:
        """Return the cache file path for image_id."""
        return self.directory / f"{image_id}.json"


def safe_namespace(namespace: str) -> str:
    """Return a filesystem-safe cache namespace."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", namespace).strip("._")
    return safe or "default"
