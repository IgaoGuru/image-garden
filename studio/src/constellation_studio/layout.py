"""Backend UMAP layout helpers for persisted runtime positions."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import cast

Embedding = tuple[float, ...]
Vec3 = tuple[float, float, float]


def positions_from_embeddings(
    asset_embeddings: Mapping[str, Embedding],
    *,
    radius: float = 120.0,
) -> dict[str, Vec3]:
    """Project embedding vectors to 3D positions with UMAP."""
    if not asset_embeddings:
        return {}
    ids = sorted(asset_embeddings)
    embeddings = [asset_embeddings[asset_id] for asset_id in ids]
    projected = umap_project(embeddings)
    normalized = normalize_positions(projected, radius=radius)
    return dict(zip(ids, normalized, strict=True))


def umap_project(embeddings: Sequence[Embedding]) -> list[Vec3]:
    """Project embeddings to three dimensions using UMAP.

    UMAP needs more than a couple samples. Tiny sets use simple non-random
    geometry because there is no meaningful manifold to estimate.
    """
    count = len(embeddings)
    if count == 0:
        return []
    if count == 1:
        return [(0.0, 0.0, 0.0)]
    if count == 2:
        return [(-1.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
    if count == 3:
        return [
            (
                math.cos(index * 2.0 * math.pi / 3.0),
                math.sin(index * 2.0 * math.pi / 3.0),
                0.0,
            )
            for index in range(3)
        ]

    import numpy as np
    from umap import UMAP

    max_dim = max((len(embedding) for embedding in embeddings), default=0)
    if max_dim == 0:
        msg = "cannot layout empty embedding vectors"
        raise ValueError(msg)

    matrix = np.zeros((count, max_dim), dtype=np.float32)
    for row_index, embedding in enumerate(embeddings):
        matrix[row_index, : len(embedding)] = np.asarray(
            embedding, dtype=np.float32
        )

    neighbors = max(2, min(15, count - 1))
    reducer = UMAP(
        n_components=3,
        n_neighbors=neighbors,
        min_dist=0.08,
        metric="cosine",
        random_state=42,
    )
    projected = cast(
        "Sequence[Sequence[float]]", reducer.fit_transform(matrix)
    )
    return [(float(row[0]), float(row[1]), float(row[2])) for row in projected]


def normalize_positions(
    positions: Sequence[Vec3], *, radius: float
) -> list[Vec3]:
    """Center and scale positions into a friendly viewer radius."""
    if not positions:
        return []
    cx = sum(position[0] for position in positions) / len(positions)
    cy = sum(position[1] for position in positions) / len(positions)
    cz = sum(position[2] for position in positions) / len(positions)
    centered = [
        (position[0] - cx, position[1] - cy, position[2] - cz)
        for position in positions
    ]
    max_distance = max(
        math.sqrt((x * x) + (y * y) + (z * z)) for x, y, z in centered
    )
    if max_distance == 0:
        return centered
    scale = radius / max_distance
    return [(x * scale, y * scale, z * scale) for x, y, z in centered]
