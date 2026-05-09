from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from constellation_studio import download_onnx
from constellation_studio.download_onnx import (
    DEFAULT_ONNX_MODEL_ID,
    DEFAULT_ONNX_OUTPUT,
    OnnxModelDefinition,
    build_parser,
)


class FakeResponse:
    """Small context-manager response for downloader tests."""

    headers: Mapping[str, str]

    def __init__(self, payload: bytes) -> None:
        self._stream = BytesIO(payload)
        self.headers = {"content-length": str(len(payload))}

    def __enter__(self) -> Self:
        """Return response context."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close response context."""
        _ = (exc_type, exc, traceback)

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


def test_download_onnx_parser_defaults_to_mobileclip_s1() -> None:
    parser = build_parser()
    args = parser.parse_args([])

    assert args.model == DEFAULT_ONNX_MODEL_ID
    assert args.output is None
    assert DEFAULT_ONNX_OUTPUT.as_posix() == "models/mobileclip-s1-vision.onnx"


def test_download_onnx_parser_allows_legacy_clip_model() -> None:
    parser = build_parser()
    args = parser.parse_args(["--model", "clip-vit-base-patch32"])

    assert args.model == "clip-vit-base-patch32"


def test_download_onnx_parser_allows_custom_url() -> None:
    parser = build_parser()
    args = parser.parse_args(["--url", "https://example.test/model.onnx"])

    assert args.url == "https://example.test/model.onnx"


def test_download_onnx_model_downloads_preprocessor_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = OnnxModelDefinition(
        id="test-mobileclip",
        display_name="Test MobileCLIP",
        repo="example/mobileclip",
        file="onnx/vision_model.onnx",
        url="https://example.test/vision_model.onnx",
        output=Path("models/test.onnx"),
        minimum_bytes=4,
        docs_url="https://example.test/docs",
        license_url="https://example.test/license",
        preprocessor_config_url="https://example.test/preprocessor_config.json",
    )
    payloads = {
        model.url: b"model-bytes",
        model.preprocessor_config_url: b'{"crop_size":{"height":256}}',
    }
    calls: list[str] = []

    def fake_urlopen(
        request: urllib.request.Request,
        timeout: int,
    ) -> FakeResponse:
        _ = timeout
        calls.append(request.full_url)
        payload = payloads[request.full_url]
        return FakeResponse(payload)

    monkeypatch.setitem(download_onnx.ONNX_MODEL_DEFINITIONS, model.id, model)
    monkeypatch.setattr(download_onnx.urllib.request, "urlopen", fake_urlopen)

    output = download_onnx.download_onnx_model(
        tmp_path / "test.onnx",
        model_id=model.id,
    )

    assert output.read_bytes() == b"model-bytes"
    assert (tmp_path / "preprocessor_config.json").read_bytes() == payloads[
        model.preprocessor_config_url
    ]
    assert calls == [model.url, model.preprocessor_config_url]

    metadata = json.loads(
        output.with_suffix(output.suffix + ".json").read_text("utf-8")
    )
    assert metadata["modelId"] == model.id
    assert metadata["source"] == model.url
    assert metadata["licenseUrl"] == model.license_url
    assert metadata["model"]["bytes"] == len(b"model-bytes")
    assert (
        metadata["preprocessorConfig"]["source"]
        == model.preprocessor_config_url
    )


def test_download_onnx_model_rejects_small_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = OnnxModelDefinition(
        id="test-large",
        display_name="Test Large",
        repo="example/large",
        file="onnx/vision_model.onnx",
        url="https://example.test/large.onnx",
        output=Path("models/large.onnx"),
        minimum_bytes=100,
        docs_url="https://example.test/docs",
        license_url="https://example.test/license",
    )

    def fake_urlopen(
        request: urllib.request.Request,
        timeout: int,
    ) -> FakeResponse:
        _ = (request, timeout)
        return FakeResponse(b"tiny")

    monkeypatch.setitem(download_onnx.ONNX_MODEL_DEFINITIONS, model.id, model)
    monkeypatch.setattr(download_onnx.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="unexpectedly small"):
        download_onnx.download_onnx_model(
            tmp_path / "large.onnx", model_id=model.id
        )
