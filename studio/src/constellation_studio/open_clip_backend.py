"""open_clip image embedding backend.

This module intentionally contains the untyped third-party boundary for torch,
Pillow, and open_clip. It is excluded from basedpyright strict checks; the rest
of Studio keeps precise typed interfaces around it.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


def default_device() -> str:
    """Return the best available torch device name."""
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class OpenClipImageEmbedder:
    """Batch image embedder backed by open_clip."""

    def __init__(
        self,
        *,
        model: str,
        pretrained: str,
        device: str,
    ) -> None:
        import open_clip
        import torch

        self._torch: Any = torch
        self._device = device
        created = open_clip.create_model_and_transforms(
            model,
            pretrained=pretrained,
            device=device,
        )
        self._model: Any = created[0]
        self._preprocess: Any = created[2]
        self._model.eval()

    @property
    def device(self) -> str:
        """Torch device used by this embedder."""
        return self._device

    def embed_images(self, paths: Sequence[Path]) -> list[tuple[float, ...]]:
        """Embed one batch of image paths."""
        torch = self._torch
        tensors = self._preprocess_images(paths)
        batch = torch.stack(tensors).to(self._device)
        with torch.no_grad():
            features = self._model.encode_image(batch)
            features = features / features.norm(dim=-1, keepdim=True)
            features = features.detach().cpu().float().numpy()

        return [
            tuple(float(value) for value in row.tolist()) for row in features
        ]

    def _preprocess_images(self, paths: Sequence[Path]) -> list[object]:
        """Load and preprocess image batch using CPU worker threads."""
        if len(paths) <= 1:
            return [self._preprocess_image(path) for path in paths]
        worker_count = min(len(paths), os.process_cpu_count() or 1, 8)
        if worker_count <= 1:
            return [self._preprocess_image(path) for path in paths]
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            return list(executor.map(self._preprocess_image, paths))

    def _preprocess_image(self, path: Path) -> object:
        """Load and preprocess one image."""
        try:
            with Image.open(path) as image:
                normalized = ImageOps.exif_transpose(image).convert("RGB")
                return self._preprocess(normalized)
        except OSError as exc:
            msg = f"failed to load image {path}: {exc}"
            raise RuntimeError(msg) from exc
