from __future__ import annotations

import json
import sys
import types
from collections.abc import Sequence
from pathlib import Path

import pytest
from PIL import Image

from constellation_studio import assets as assets_module
from constellation_studio.assets import (
    AssetOptions,
    PreparedSanitizedImage,
    sanitize_directory,
)
from constellation_studio.cache import EmbeddingCache
from constellation_studio.embed import EmbedOptions, embed_directory
from constellation_studio.images import (
    discover_image_files,
    image_url,
    quote_path,
)
from constellation_studio.schema import (
    read_studio_manifest,
    write_constellation_json,
    write_studio_manifest,
)


class FakeEmbedder:
    cache_namespace = "fake"

    def __init__(self) -> None:
        self.calls: list[list[Path]] = []

    def embed_images(self, paths: Sequence[Path]) -> list[tuple[float, ...]]:
        self.calls.append(list(paths))
        return [
            (float(index), float(path.stat().st_size))
            for index, path in enumerate(paths)
        ]


class FailingEmbedder:
    cache_namespace = "failing"

    def __init__(self) -> None:
        self.single_calls = 0

    def embed_images(self, paths: Sequence[Path]) -> list[tuple[float, ...]]:
        if len(paths) > 1:
            msg = "bad test batch"
            raise RuntimeError(msg)
        self.single_calls += 1
        if self.single_calls == 2:
            msg = "bad test image"
            raise RuntimeError(msg)
        return [(float(path.stat().st_size),) for path in paths]


def create_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 6), color).save(path)


def test_discover_image_files_is_recursive_and_sorted(tmp_path: Path) -> None:
    create_image(tmp_path / "b.jpg", (255, 0, 0))
    create_image(tmp_path / "nested" / "a.png", (0, 255, 0))
    (tmp_path / "ignore.txt").write_text("not an image", encoding="utf-8")

    discovered = discover_image_files(tmp_path)

    assert [path.relative_to(tmp_path).as_posix() for path in discovered] == [
        "b.jpg",
        "nested/a.png",
    ]


def test_discover_image_files_includes_heic_extensions(tmp_path: Path) -> None:
    (tmp_path / "photo.HEIC").write_bytes(b"not real heic")
    (tmp_path / "photo.heif").write_bytes(b"not real heif")

    assert [path.name for path in discover_image_files(tmp_path)] == [
        "photo.HEIC",
        "photo.heif",
    ]


def test_image_url_quotes_path_segments(tmp_path: Path) -> None:
    path = tmp_path / "space dir" / "é image.jpg"
    create_image(path, (0, 0, 255))

    assert (
        quote_path("space dir/é image.jpg") == "space%20dir/%C3%A9%20image.jpg"
    )
    assert image_url(path, tmp_path, "/images") == (
        "/images/space%20dir/%C3%A9%20image.jpg"
    )


def test_sanitize_directory_writes_hashed_jpegs_and_thumbnails(
    tmp_path: Path,
) -> None:
    create_image(tmp_path / "raw" / "one.png", (255, 0, 0))
    create_image(tmp_path / "raw" / "duplicate.jpg", (255, 0, 0))
    warnings: list[str] = []

    sanitized = sanitize_directory(
        tmp_path / "raw",
        options=AssetOptions(
            asset_root=tmp_path / "assets",
            warn=warnings.append,
        ),
    )

    assert len(sanitized) == 1
    record = sanitized[0]
    assert len(record.id) == 64
    assert (
        record.image_path
        == tmp_path / "assets" / "images" / f"{record.id}.jpg"
    )
    assert (
        record.thumbnail_path
        == tmp_path / "assets" / "thumbs" / f"{record.id}.jpg"
    )
    assert record.url == f"/assets/images/{record.id}.jpg"
    assert record.thumbnail_url == f"/assets/thumbs/{record.id}.jpg"
    assert record.image_path.read_bytes().startswith(b"\xff\xd8")
    assert record.thumbnail_path.read_bytes().startswith(b"\xff\xd8")
    assert warnings[-1] == "Skipped 1 image(s) during ingestion."


def test_sanitize_directory_writes_each_result_before_next_prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_image(tmp_path / "raw" / "one.jpg", (255, 0, 0))
    create_image(tmp_path / "raw" / "two.jpg", (0, 255, 0))
    original = assets_module.prepare_sanitized_image
    prepared: list[PreparedSanitizedImage] = []

    def wrapped_prepare(
        path: Path, *, options: AssetOptions
    ) -> PreparedSanitizedImage:
        if prepared:
            assert prepared[0].record.image_path.is_file()
            assert prepared[0].record.thumbnail_path.is_file()
        result = original(path, options=options)
        prepared.append(result)
        return result

    monkeypatch.setattr(
        assets_module,
        "prepare_sanitized_image",
        wrapped_prepare,
    )

    sanitized = sanitize_directory(
        tmp_path / "raw",
        options=AssetOptions(
            asset_root=tmp_path / "assets",
            sanitize_workers=1,
        ),
    )

    assert len(sanitized) == 2


def test_embed_directory_writes_viewer_contract(tmp_path: Path) -> None:
    create_image(tmp_path / "photos" / "one.jpg", (255, 0, 0))
    create_image(tmp_path / "photos" / "two.png", (0, 255, 0))
    progress_events: list[tuple[int, int]] = []

    embedded = embed_directory(
        tmp_path / "photos",
        embedder=FakeEmbedder(),
        options=EmbedOptions(
            asset_root=tmp_path / "assets",
            batch_size=1,
            progress=lambda completed, total: progress_events.append(
                (completed, total)
            ),
        ),
    )
    output = tmp_path / "constellation.json"
    write_constellation_json(output, embedded)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert list(payload) == ["images"]
    ids = [image["id"] for image in payload["images"]]
    assert len(ids) == 2
    assert all(len(image_id) == 64 for image_id in ids)
    assert payload["images"][0]["url"].startswith("/assets/images/")
    assert payload["images"][0]["thumbnailUrl"].startswith(
        "/assets/thumbs/",
    )
    assert payload["images"][0]["width"] == 8
    assert payload["images"][0]["height"] == 6
    assert payload["images"][0]["metadata"]["sourcePath"].endswith(
        ("one.jpg", "two.png"),
    )
    assert len(payload["images"][0]["embedding"]) == 2
    assert progress_events == [(1, 2), (2, 2)]

    manifest_path = write_studio_manifest(
        data_path=output,
        image_root=tmp_path / "assets",
        url_prefix="/assets/",
    )
    manifest = read_studio_manifest(output)
    assert manifest_path.name == "constellation.studio.json"
    assert manifest["imageRoot"] == str((tmp_path / "assets").resolve())
    assert manifest["dataJson"] == str(output.resolve())


def test_embed_directory_reuses_embedding_cache(tmp_path: Path) -> None:
    create_image(tmp_path / "photos" / "one.jpg", (255, 0, 0))
    first_embedder = FakeEmbedder()
    options = EmbedOptions(
        asset_root=tmp_path / "assets",
        cache_namespace="test-cache",
    )

    first = embed_directory(
        tmp_path / "photos",
        embedder=first_embedder,
        options=options,
    )
    second_embedder = FakeEmbedder()
    warnings: list[str] = []
    second = embed_directory(
        tmp_path / "photos",
        embedder=second_embedder,
        options=EmbedOptions(
            asset_root=tmp_path / "assets",
            cache_namespace="test-cache",
            warn=warnings.append,
        ),
    )

    assert [image.id for image in second] == [image.id for image in first]
    assert [image.embedding for image in second] == [
        image.embedding for image in first
    ]
    assert first_embedder.calls
    assert second_embedder.calls == []
    assert warnings == ["Reused 1 cached embedding(s)."]
    assert EmbeddingCache(tmp_path / "assets", "test-cache").get(first[0].id)


def test_embed_directory_can_skip_bad_images_without_misaligning_ids(
    tmp_path: Path,
) -> None:
    create_image(tmp_path / "photos" / "a.jpg", (255, 0, 0))
    create_image(tmp_path / "photos" / "bad.jpg", (0, 255, 0))
    create_image(tmp_path / "photos" / "c.jpg", (0, 0, 255))
    warnings: list[str] = []

    embedded = embed_directory(
        tmp_path / "photos",
        embedder=FailingEmbedder(),
        options=EmbedOptions(
            asset_root=tmp_path / "assets",
            batch_size=3,
            skip_errors=True,
            warn=warnings.append,
            use_cache=False,
        ),
    )

    assert len(embedded) == 2
    assert [len(image.id) for image in embedded] == [64, 64]
    assert all(len(image.embedding) == 1 for image in embedded)
    assert any("bad test image" in warning for warning in warnings)
    assert warnings[-1] == "Skipped 1 image(s) that failed to embed."


def test_onnx_preflight_reports_missing_model_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from constellation_studio.embedding_providers import (
        OnnxClipEmbeddingProvider,
        preflight_embedding_provider,
    )

    fake_onnxruntime = types.SimpleNamespace(
        get_available_providers=lambda: ["CPUExecutionProvider"],
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_onnxruntime)

    provider = OnnxClipEmbeddingProvider(
        model_path=tmp_path / "missing.onnx",
        provider="auto",
    )
    with pytest.raises(FileNotFoundError, match="ONNX model file does not exist"):
        preflight_embedding_provider(provider)
