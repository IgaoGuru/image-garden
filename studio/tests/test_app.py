from __future__ import annotations

from pathlib import Path

from constellation_studio.app import (
    find_bundled_onnx_model,
    find_bundled_playview_dist,
)


def test_find_bundled_onnx_model_prefers_mobileclip_s1(
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    models.mkdir()
    legacy = models / "clip-image-encoder.onnx"
    mobileclip = models / "mobileclip-s1-vision.onnx"
    legacy.write_bytes(b"legacy model")
    mobileclip.write_bytes(b"mobileclip model")

    assert find_bundled_onnx_model(tmp_path) == mobileclip.resolve()


def test_find_bundled_onnx_model_falls_back_to_legacy_clip(
    tmp_path: Path,
) -> None:
    model = tmp_path / "models" / "clip-image-encoder.onnx"
    model.parent.mkdir()
    model.write_bytes(b"not a real model")

    assert find_bundled_onnx_model(tmp_path) == model.resolve()


def test_find_bundled_onnx_model_returns_none_without_model(
    tmp_path: Path,
) -> None:
    assert find_bundled_onnx_model(tmp_path) is None


def test_find_bundled_playview_dist_checks_repo_dist(tmp_path: Path) -> None:
    dist = tmp_path / "playview" / "dist"
    dist.mkdir(parents=True)

    assert find_bundled_playview_dist(tmp_path) == dist.resolve()
