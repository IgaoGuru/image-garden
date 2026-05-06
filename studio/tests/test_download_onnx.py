from __future__ import annotations

from pathlib import Path

from constellation_studio.download_onnx import (
    DEFAULT_ONNX_OUTPUT,
    build_parser,
)


def test_download_onnx_parser_defaults_to_models_dir() -> None:
    parser = build_parser()
    args = parser.parse_args([])

    assert args.output == DEFAULT_ONNX_OUTPUT
    assert Path(args.output).as_posix() == "models/clip-image-encoder.onnx"


def test_download_onnx_parser_allows_custom_url() -> None:
    parser = build_parser()
    args = parser.parse_args(["--url", "https://example.test/model.onnx"])

    assert args.url == "https://example.test/model.onnx"
