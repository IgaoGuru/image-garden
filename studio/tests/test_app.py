from __future__ import annotations

from pathlib import Path

from constellation_studio.app import find_bundled_onnx_model


def test_find_bundled_onnx_model_checks_models_dir(tmp_path: Path) -> None:
    model = tmp_path / "models" / "clip-image-encoder.onnx"
    model.parent.mkdir()
    model.write_bytes(b"not a real model")

    assert find_bundled_onnx_model(tmp_path) == model.resolve()


def test_find_bundled_onnx_model_returns_none_without_model(
    tmp_path: Path,
) -> None:
    assert find_bundled_onnx_model(tmp_path) is None
