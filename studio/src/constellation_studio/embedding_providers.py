"""Embedding provider boundaries and concrete local providers."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from PIL import Image, ImageOps

from constellation_studio.open_clip_backend import (
    OpenClipImageEmbedder,
    default_device,
)

Embedding = tuple[float, ...]
EmbeddingEngine = Literal["none", "openclip", "onnx"]
RgbVector = tuple[float, float, float]

CLIP_IMAGE_MEAN: RgbVector = (0.48145466, 0.4578275, 0.40821073)
CLIP_IMAGE_STD: RgbVector = (0.26862954, 0.26130258, 0.27577711)


@dataclass(frozen=True, slots=True)
class OnnxPreprocessOptions:
    """Image preprocessing options for an ONNX vision encoder."""

    image_size: int
    mean: RgbVector
    std: RgbVector
    rescale_factor: float = 1.0 / 255.0

    @property
    def cache_key(self) -> str:
        """Return stable preprocessing cache key."""
        values = [
            str(self.image_size),
            f"{self.rescale_factor:g}",
            *(f"{value:g}" for value in self.mean),
            *(f"{value:g}" for value in self.std),
        ]
        return "_".join(values)


class EmbeddingProvider(Protocol):
    """Provider interface for image embedding engines."""

    @property
    def cache_namespace(self) -> str:
        """Return a stable cache namespace for this engine/model/config."""
        raise NotImplementedError

    def embed_images(self, paths: Sequence[Path]) -> list[Embedding]:
        """Embed image paths in order."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class DeterministicEmbeddingProvider:
    """Small deterministic provider for tests and no-model smoke runs."""

    dimensions: int = 32

    @property
    def cache_namespace(self) -> str:
        """Return this provider's cache namespace."""
        return f"deterministic/{self.dimensions}"

    def embed_images(self, paths: Sequence[Path]) -> list[Embedding]:
        """Return deterministic pseudo-embeddings for local smoke tests."""
        return [
            deterministic_embedding(path, self.dimensions) for path in paths
        ]


@dataclass(frozen=True, slots=True)
class OpenClipEmbeddingProvider:
    """PyTorch/OpenCLIP embedding provider for advanced/dev use."""

    model: str
    pretrained: str
    device: str = "auto"
    _embedder: OpenClipImageEmbedder | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def cache_namespace(self) -> str:
        """Return this provider's cache namespace."""
        resolved_device = (
            default_device() if self.device == "auto" else self.device
        )
        return f"open_clip/{self.model}/{self.pretrained}/{resolved_device}"

    def embed_images(self, paths: Sequence[Path]) -> list[Embedding]:
        """Embed images using OpenCLIP."""
        return self.embedder.embed_images(paths)

    @property
    def embedder(self) -> OpenClipImageEmbedder:
        """Return a lazily created OpenCLIP embedder."""
        if self._embedder is None:
            resolved_device = (
                default_device() if self.device == "auto" else self.device
            )
            object.__setattr__(
                self,
                "_embedder",
                OpenClipImageEmbedder(
                    model=self.model,
                    pretrained=self.pretrained,
                    device=resolved_device,
                ),
            )
        embedder = self._embedder
        if embedder is None:  # pragma: no cover - defensive
            msg = "failed to initialize OpenCLIP embedder"
            raise RuntimeError(msg)
        return embedder


@dataclass(frozen=True, slots=True)
class OnnxClipEmbeddingProvider:
    """ONNX Runtime CLIP-like image embedding provider.

    This provider intentionally imports ``onnxruntime`` lazily so core Studio
    and tests do not require ONNX installed. It expects an ONNX image encoder
    with an NCHW ``pixel_values`` input and at least one 2D embedding output.
    """

    model_path: Path
    provider: str = "auto"
    image_size: int = 224
    _session: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _input_name: str | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def cache_namespace(self) -> str:
        """Return this provider's cache namespace."""
        resolved = self.model_path.expanduser().resolve()
        fingerprint = file_fingerprint(resolved)
        preprocess = self.preprocess_options
        return (
            f"onnx/{resolved.name}/{fingerprint}/{self.provider}/"
            f"{preprocess.cache_key}"
        )

    @property
    def preprocess_options(self) -> OnnxPreprocessOptions:
        """Return preprocessing options from a sibling HF config if present."""
        return read_onnx_preprocess_options(
            self.model_path,
            default_image_size=self.image_size,
        )

    def embed_images(self, paths: Sequence[Path]) -> list[Embedding]:
        """Embed images using ONNX Runtime."""
        session, input_name = self.session_info
        batch = preprocess_clip_images(paths, options=self.preprocess_options)
        outputs = session.run(None, {input_name: batch})
        return normalize_embedding_rows(select_embedding_output(outputs))

    def preflight(self) -> None:
        """Validate ONNX Runtime, model file, and provider before serving."""
        _session, _input_name = self.session_info

    @property
    def session_info(self) -> tuple[Any, str]:
        """Return a lazily created ONNX Runtime session and IO names."""
        if self._session is None:
            try:
                import onnxruntime as ort  # pyright: ignore[reportMissingImports]
            except (
                ImportError
            ) as exc:  # pragma: no cover - environment dependent
                raise RuntimeError(onnx_runtime_missing_message()) from exc

            model_path = self.model_path.expanduser().resolve()
            if not model_path.is_file():
                msg = f"ONNX model file does not exist: {model_path}"
                raise FileNotFoundError(msg)

            ort_module = cast("Any", ort)
            get_available_providers = cast(
                "Callable[[], list[str]]",
                ort_module.get_available_providers,
            )
            session = ort_module.InferenceSession(
                str(model_path),
                providers=onnx_providers(
                    get_available_providers,
                    self.provider,
                ),
            )
            input_name = str(session.get_inputs()[0].name)
            object.__setattr__(self, "_session", session)
            object.__setattr__(self, "_input_name", input_name)
        if self._session is None or self._input_name is None:
            msg = "failed to initialize ONNX Runtime session"
            raise RuntimeError(msg)
        return self._session, self._input_name


def create_embedding_provider(  # noqa: PLR0913
    *,
    engine: str,
    model: str,
    pretrained: str,
    device: str,
    onnx_model: Path | None = None,
    onnx_provider: str = "auto",
) -> EmbeddingProvider | None:
    """Create an embedding provider from CLI/backend settings."""
    normalized = engine.strip().lower()
    if normalized in {"", "none", "off", "disabled"}:
        return None
    if normalized in {"deterministic", "fake"}:
        return DeterministicEmbeddingProvider()
    if normalized in {"openclip", "open_clip", "torch", "pytorch"}:
        return OpenClipEmbeddingProvider(
            model=model,
            pretrained=pretrained,
            device=device,
        )
    if normalized == "onnx":
        if onnx_model is None:
            msg = "--onnx-model is required when --embedding-engine=onnx"
            raise ValueError(msg)
        return OnnxClipEmbeddingProvider(
            model_path=onnx_model,
            provider=onnx_provider,
        )
    msg = f"unknown embedding engine: {engine}"
    raise ValueError(msg)


def preflight_embedding_provider(provider: EmbeddingProvider | None) -> None:
    """Fail fast for provider/runtime issues before opening the app UI."""
    if isinstance(provider, OnnxClipEmbeddingProvider):
        provider.preflight()


def ensure_onnx_runtime_available() -> None:
    """Raise a friendly error when the ONNX extra is not installed."""
    if importlib.util.find_spec("onnxruntime") is None:
        raise RuntimeError(onnx_runtime_missing_message())


def onnx_runtime_missing_message() -> str:
    """Return the user-facing ONNX Runtime installation guidance."""
    return (
        "ONNX Runtime is not installed. Run `pnpm studio:sync` or "
        "`uv --project studio sync --extra onnx`, then restart "
        "Constellation. You can also choose the OpenCLIP advanced engine "
        "with `--embedding-engine openclip`."
    )


def deterministic_embedding(path: Path, dimensions: int) -> Embedding:
    """Return a stable pseudo-vector from a path and file metadata."""
    resolved = path.expanduser().resolve()
    try:
        stat = resolved.stat()
        key = f"{resolved}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        key = str(resolved)
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(f"{key}:{counter}".encode()).digest()
        for byte in digest:
            values.append((float(byte) / 127.5) - 1.0)
            if len(values) == dimensions:
                break
        counter += 1
    return tuple(values)


def file_fingerprint(path: Path) -> str:
    """Return a lightweight model file fingerprint for cache namespacing."""
    stat = path.stat()
    payload = f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def onnx_providers(
    get_available_providers: Callable[[], list[str]],
    provider: str,
) -> list[str] | None:
    """Return ONNX Runtime providers from a friendly provider selector."""
    if provider == "auto":
        return None
    available = get_available_providers()
    requested = {
        "cpu": "CPUExecutionProvider",
        "cuda": "CUDAExecutionProvider",
        "directml": "DmlExecutionProvider",
        "coreml": "CoreMLExecutionProvider",
    }.get(provider.lower(), provider)
    if requested not in available:
        msg = f"ONNX provider {requested!r} is not available; available: {available}"
        raise RuntimeError(msg)
    return [requested]


def read_onnx_preprocess_options(
    model_path: Path,
    *,
    default_image_size: int,
) -> OnnxPreprocessOptions:
    """Read Hugging Face image preprocessor options beside an ONNX model."""
    config_path = onnx_preprocessor_config_path(model_path)
    if config_path is None:
        return OnnxPreprocessOptions(
            image_size=default_image_size,
            mean=CLIP_IMAGE_MEAN,
            std=CLIP_IMAGE_STD,
        )
    loaded = cast("object", json.loads(config_path.read_text("utf-8")))
    if not isinstance(loaded, Mapping):
        msg = f"invalid ONNX preprocessor config: {config_path}"
        raise ValueError(msg)
    config = cast("Mapping[str, object]", loaded)
    normalize = config.get("do_normalize") is not False
    rescale = config.get("do_rescale") is not False
    return OnnxPreprocessOptions(
        image_size=preprocessor_image_size(config, default_image_size),
        mean=preprocessor_rgb_vector(config.get("image_mean"), CLIP_IMAGE_MEAN)
        if normalize
        else (0.0, 0.0, 0.0),
        std=preprocessor_rgb_vector(config.get("image_std"), CLIP_IMAGE_STD)
        if normalize
        else (1.0, 1.0, 1.0),
        rescale_factor=preprocessor_float(
            config.get("rescale_factor"),
            1.0 / 255.0,
        )
        if rescale
        else 1.0,
    )


def onnx_preprocessor_config_path(model_path: Path) -> Path | None:
    """Return nearest preprocessor_config.json for an ONNX model."""
    resolved = model_path.expanduser().resolve()
    candidates = [
        resolved.with_name("preprocessor_config.json"),
        resolved.parent.parent / "preprocessor_config.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def preprocessor_image_size(
    config: Mapping[str, object],
    default: int,
) -> int:
    """Return square image size from HF preprocessor config."""
    crop_size = config.get("crop_size")
    if isinstance(crop_size, Mapping):
        typed_crop_size = cast("Mapping[str, object]", crop_size)
        height = typed_crop_size.get("height")
        if isinstance(height, int) and not isinstance(height, bool):
            return height
    size = config.get("size")
    if isinstance(size, Mapping):
        typed_size = cast("Mapping[str, object]", size)
        shortest_edge = typed_size.get("shortest_edge")
        if isinstance(shortest_edge, int) and not isinstance(
            shortest_edge, bool
        ):
            return shortest_edge
        height = typed_size.get("height")
        if isinstance(height, int) and not isinstance(height, bool):
            return height
    if isinstance(size, int) and not isinstance(size, bool):
        return size
    return default


def preprocessor_rgb_vector(value: object, default: RgbVector) -> RgbVector:
    """Return a three-element RGB vector from config data."""
    if not isinstance(value, list | tuple):
        return default
    values = cast("Sequence[object]", value)
    if len(values) != 3:
        return default
    converted: list[float] = []
    for item in values:
        if isinstance(item, bool) or not isinstance(item, int | float):
            return default
        converted.append(float(item))
    return (converted[0], converted[1], converted[2])


def preprocessor_float(value: object, default: float) -> float:
    """Return a float from config data."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value)


def preprocess_clip_images(
    paths: Sequence[Path],
    *,
    options: OnnxPreprocessOptions,
) -> object:
    """Preprocess images into a CLIP-style NCHW float32 batch."""
    import numpy as np

    mean = np.array(options.mean, dtype=np.float32)
    std = np.array(options.std, dtype=np.float32)
    images: list[Any] = []
    for path in paths:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        image = resize_center_crop(image, options.image_size)
        array = np.asarray(image, dtype=np.float32) * options.rescale_factor
        array = (array - mean) / std
        images.append(np.transpose(array, (2, 0, 1)))
    return np.stack(images, axis=0).astype(np.float32)


def resize_center_crop(image: Image.Image, size: int) -> Image.Image:
    """Resize preserving aspect ratio, then center-crop to a square."""
    width, height = image.size
    scale = size / float(min(width, height))
    resized = image.resize(
        (round(width * scale), round(height * scale)),
        Image.Resampling.BICUBIC,
    )
    left = max(0, (resized.width - size) // 2)
    top = max(0, (resized.height - size) // 2)
    return resized.crop((left, top, left + size, top + size))


def select_embedding_output(outputs: Sequence[object]) -> object:
    """Choose the 2D embedding tensor from ONNX outputs."""
    import numpy as np

    for output in outputs:
        array = np.asarray(output)
        if array.ndim == 2:
            return output
    shapes = [tuple(np.asarray(output).shape) for output in outputs]
    msg = f"ONNX model did not return a 2D embedding output; got {shapes}"
    raise RuntimeError(msg)


def normalize_embedding_rows(raw: object) -> list[Embedding]:
    """Convert ONNX output to L2-normalized embedding tuples."""
    import numpy as np

    array = np.asarray(raw, dtype=np.float32)
    if array.ndim != 2:
        msg = f"ONNX embedding output must be 2D, got shape {array.shape}"
        raise RuntimeError(msg)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized = array / norms
    return [tuple(float(value) for value in row) for row in normalized]
