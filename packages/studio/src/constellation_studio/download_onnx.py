"""Download ONNX image encoders used by Image Garden."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import shutil
import sys
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import NotRequired, Protocol, TypedDict, cast
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class OnnxModelDefinition:
    """One downloadable ONNX image encoder."""

    id: str
    display_name: str
    repo: str
    file: str
    url: str
    output: Path
    minimum_bytes: int
    docs_url: str
    revision: str = "main"
    sha256: str | None = None
    license_url: str | None = None
    preprocessor_config_url: str | None = None


class DownloadedFileMetadata(TypedDict):
    """Metadata for one downloaded or reused file."""

    path: str
    source: str
    sha256: str
    bytes: int


class DownloadMetadata(TypedDict):
    """Metadata written beside downloaded ONNX models."""

    modelId: str
    modelName: str
    repo: str
    file: str
    revision: str
    source: str
    docsUrl: str
    downloadedAt: str
    licenseUrl: NotRequired[str]
    expectedSha256: NotRequired[str]
    minimumBytes: int
    model: DownloadedFileMetadata
    preprocessorConfig: NotRequired[DownloadedFileMetadata]


class DownloadResponse(Protocol):
    """Minimal binary response protocol used while streaming downloads."""

    headers: Mapping[str, str]

    def read(self, size: int = -1) -> bytes:
        """Read up to size bytes."""
        raise NotImplementedError


MOBILECLIP_S1_REVISION = "48c0a9f89ff272544d1f2595483011a183fd1a52"
MOBILECLIP_S1_SHA256 = (
    "5dece7da38f907d440f91c86cca841919c5a1affd2329ca0e94f8593fd0bfbfb"
)
MOBILECLIP_S1_ONNX = OnnxModelDefinition(
    id="mobileclip-s1",
    display_name="MobileCLIP-S1 ONNX image encoder",
    repo="Xenova/mobileclip_s1",
    file="onnx/vision_model.onnx",
    url=(
        "https://huggingface.co/Xenova/mobileclip_s1/resolve/"
        f"{MOBILECLIP_S1_REVISION}/onnx/vision_model.onnx"
    ),
    output=Path("models/mobileclip-s1-vision.onnx"),
    minimum_bytes=80_000_000,
    docs_url="https://huggingface.co/Xenova/mobileclip_s1",
    revision=MOBILECLIP_S1_REVISION,
    sha256=MOBILECLIP_S1_SHA256,
    license_url=(
        "https://huggingface.co/Xenova/mobileclip_s1/resolve/"
        f"{MOBILECLIP_S1_REVISION}/LICENSE"
    ),
    preprocessor_config_url=(
        "https://huggingface.co/Xenova/mobileclip_s1/resolve/"
        f"{MOBILECLIP_S1_REVISION}/preprocessor_config.json"
    ),
)
CLIP_VIT_B32_SHA256 = (
    "fd6e1402a588279d1723c7534d4bcba5bc0b14b47dfab0e46f8c47b8270d7d40"
)
CLIP_VIT_B32_ONNX = OnnxModelDefinition(
    id="clip-vit-base-patch32",
    display_name="CLIP ViT-B/32 ONNX image encoder",
    repo="Xenova/clip-vit-base-patch32",
    file="onnx/vision_model.onnx",
    url=(
        "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
        "onnx/vision_model.onnx"
    ),
    output=Path("models/clip-image-encoder.onnx"),
    minimum_bytes=300_000_000,
    docs_url="https://huggingface.co/Xenova/clip-vit-base-patch32",
    sha256=CLIP_VIT_B32_SHA256,
)
ONNX_MODEL_DEFINITIONS: dict[str, OnnxModelDefinition] = {
    MOBILECLIP_S1_ONNX.id: MOBILECLIP_S1_ONNX,
    CLIP_VIT_B32_ONNX.id: CLIP_VIT_B32_ONNX,
}
DEFAULT_ONNX_MODEL_ID = MOBILECLIP_S1_ONNX.id
DEFAULT_ONNX_MODEL = MOBILECLIP_S1_ONNX
DEFAULT_ONNX_REPO = DEFAULT_ONNX_MODEL.repo
DEFAULT_ONNX_FILE = DEFAULT_ONNX_MODEL.file
DEFAULT_ONNX_URL = DEFAULT_ONNX_MODEL.url
DEFAULT_ONNX_OUTPUT = DEFAULT_ONNX_MODEL.output
DEFAULT_ONNX_SHA256 = DEFAULT_ONNX_MODEL.sha256
LEGACY_ONNX_OUTPUT = CLIP_VIT_B32_ONNX.output
USER_AGENT = "ImageGarden/0"


def build_parser() -> argparse.ArgumentParser:
    """Build parser for the ONNX model downloader."""
    parser = argparse.ArgumentParser(
        description="Download Image Garden's default ONNX image encoder.",
    )
    _ = parser.add_argument(
        "--model",
        choices=sorted(ONNX_MODEL_DEFINITIONS),
        default=DEFAULT_ONNX_MODEL_ID,
        help=f"Model to download (default: {DEFAULT_ONNX_MODEL_ID}).",
    )
    _ = parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output ONNX file path. Defaults to the selected model path "
            f"({DEFAULT_ONNX_OUTPUT} for {DEFAULT_ONNX_MODEL_ID})."
        ),
    )
    _ = parser.add_argument(
        "--url",
        default=None,
        help="Custom model URL to download instead of the selected model URL.",
    )
    _ = parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload even if output already exists.",
    )
    _ = parser.add_argument(
        "--sha256",
        default=None,
        help="Expected SHA-256 for downloaded model.",
    )
    _ = parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip SHA-256 verification for custom model URLs.",
    )
    return parser


def download_onnx_model(  # noqa: PLR0913
    output: Path | None = None,
    *,
    model_id: str = DEFAULT_ONNX_MODEL_ID,
    url: str | None = None,
    force: bool = False,
    expected_sha256: str | None = None,
    verify: bool = True,
) -> Path:
    """Download an ONNX model and companion metadata, atomically."""
    model = onnx_model_definition(model_id)
    resolved = (output or model.output).expanduser().resolve()
    model_url = url or model.url
    custom_url = url is not None and url != model.url
    expected = expected_sha256
    if verify and expected is None and not custom_url:
        expected = model.sha256
    if not verify:
        expected = None

    resolved.parent.mkdir(parents=True, exist_ok=True)
    if not resolved.is_file() or force:
        download_file(model_url, resolved)
        if not custom_url:
            validate_download_size(resolved, minimum_bytes=model.minimum_bytes)
        verify_sha256(resolved, expected)
    else:
        verify_sha256(resolved, expected)
        print(f"ONNX model already exists: {resolved}", flush=True)

    config_metadata: DownloadedFileMetadata | None = None
    if model.preprocessor_config_url is not None and not custom_url:
        config_path = resolved.parent / "preprocessor_config.json"
        if not config_path.is_file() or force:
            download_file(model.preprocessor_config_url, config_path)
        else:
            print(
                f"ONNX preprocessor config already exists: {config_path}",
                flush=True,
            )
        config_metadata = downloaded_file_metadata(
            config_path,
            source=model.preprocessor_config_url,
        )

    write_download_metadata(
        resolved,
        model=model,
        source=model_url,
        expected_sha256=expected,
        config_metadata=config_metadata,
    )
    return resolved


def onnx_model_definition(model_id: str) -> OnnxModelDefinition:
    """Return a registered ONNX model definition."""
    try:
        return ONNX_MODEL_DEFINITIONS[model_id]
    except KeyError as exc:
        choices = ", ".join(sorted(ONNX_MODEL_DEFINITIONS))
        msg = f"unknown ONNX model {model_id!r}; choices: {choices}"
        raise ValueError(msg) from exc


def download_file(url: str, destination: Path) -> None:
    """Download one HTTPS URL to destination atomically."""
    print(f"Downloading {url}", flush=True)
    print(f"Destination: {destination}", flush=True)
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https":
        msg = f"ONNX download URL must use https: {url}"
        raise ValueError(msg)

    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": USER_AGENT}
    )
    response_manager = cast(
        "contextlib.AbstractContextManager[DownloadResponse]",
        urllib.request.urlopen(request, timeout=60),  # noqa: S310
    )
    temp_path: Path | None = None
    try:
        with response_manager as response:
            total_header = response.headers.get("content-length")
            total = int(total_header) if total_header is not None else None
            temp_path = write_response_to_temp_file(
                response, destination, total
            )
        _ = shutil.move(require_temp_path(temp_path), destination)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def write_response_to_temp_file(
    response: DownloadResponse,
    destination: Path,
    total: int | None,
) -> Path:
    """Write a URL response to a temporary file beside destination."""
    with NamedTemporaryFile(
        "wb",
        delete=False,
        dir=destination.parent,
        suffix=".download",
    ) as temp_file:
        temp_path = Path(temp_file.name)
        copied = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            _ = temp_file.write(chunk)
            copied += len(chunk)
            if total:
                percent = (copied / total) * 100.0
                print(f"\r{percent:5.1f}%", end="", flush=True)
        if total:
            print(flush=True)
    return temp_path


def require_temp_path(path: Path | None) -> Path:
    """Return temp path or raise a download error."""
    if path is not None:
        return path
    msg = "ONNX download did not create a temporary file"
    raise RuntimeError(msg)


def validate_download_size(path: Path, *, minimum_bytes: int) -> None:
    """Reject clearly incomplete model downloads."""
    actual = path.stat().st_size
    if actual >= minimum_bytes:
        return
    with contextlib.suppress(OSError):
        path.unlink()
    msg = (
        f"downloaded model is unexpectedly small: {path} has {actual} "
        f"bytes, expected at least {minimum_bytes}"
    )
    raise RuntimeError(msg)


def verify_sha256(path: Path, expected: str | None) -> None:
    """Verify file digest when expected hash is provided."""
    if not expected:
        return
    actual = sha256_file(path)
    if actual.lower() == expected.lower():
        return
    with contextlib.suppress(OSError):
        path.unlink()
    msg = f"ONNX model checksum mismatch. expected {expected}, got {actual}"
    raise ValueError(msg)


def write_download_metadata(
    path: Path,
    *,
    model: OnnxModelDefinition,
    source: str,
    expected_sha256: str | None,
    config_metadata: DownloadedFileMetadata | None,
) -> None:
    """Write source/checksum metadata beside an ONNX model."""
    metadata: DownloadMetadata = {
        "modelId": model.id,
        "modelName": model.display_name,
        "repo": model.repo,
        "file": model.file,
        "revision": model.revision,
        "source": source,
        "docsUrl": model.docs_url,
        "downloadedAt": datetime.now(tz=UTC)
        .replace(microsecond=0)
        .isoformat(),
        "minimumBytes": model.minimum_bytes,
        "model": downloaded_file_metadata(path, source=source),
    }
    if model.license_url is not None:
        metadata["licenseUrl"] = model.license_url
    if expected_sha256 is not None:
        metadata["expectedSha256"] = expected_sha256
    if config_metadata is not None:
        metadata["preprocessorConfig"] = config_metadata
    _ = path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def downloaded_file_metadata(
    path: Path, *, source: str
) -> DownloadedFileMetadata:
    """Return path/source/checksum metadata for one local file."""
    return {
        "path": str(path),
        "source": source,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def sha256_file(path: Path) -> str:
    """Return SHA-256 for a local file."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    normalized_argv = list(argv if argv is not None else sys.argv[1:])
    if normalized_argv[:1] == ["--"]:
        normalized_argv = normalized_argv[1:]
    parser = build_parser()
    args = parser.parse_args(normalized_argv)
    output_arg = cast("Path | None", args.output)
    model_arg = cast("str", args.model)
    url_arg = cast("str | None", args.url)
    force_arg = cast("bool", args.force)
    no_verify_arg = cast("bool", args.no_verify)
    sha256_arg = cast("str | None", args.sha256)
    expected_sha256 = sha256_arg
    try:
        output = download_onnx_model(
            output=output_arg,
            model_id=model_arg,
            url=url_arg,
            force=force_arg,
            expected_sha256=expected_sha256,
            verify=not no_verify_arg,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"ONNX model ready: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
