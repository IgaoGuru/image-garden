from __future__ import annotations

from pathlib import Path

import pytest

from constellation_studio.download_onnx import (
    DEFAULT_ONNX_OUTPUT,
    build_parser,
    sha256_file,
    verify_sha256,
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


def test_sha256_file_and_verify(tmp_path: Path) -> None:
    path = tmp_path / "model.onnx"
    path.write_bytes(b"abc")

    digest = sha256_file(path)

    assert (
        digest
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    verify_sha256(path, digest)
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_sha256(path, "0" * 64)
