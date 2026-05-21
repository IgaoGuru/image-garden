from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from PIL import Image

from constellation_studio.backend import STUDIO_API_VERSION, run_test_backend
from constellation_studio.embedding_providers import (
    DeterministicEmbeddingProvider,
)


def create_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 6), color).save(path)


def fetch_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: object) -> object:
    request = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def test_playview_studio_api_contract(tmp_path: Path) -> None:
    create_image(tmp_path / "photos" / "one.jpg", (255, 0, 0))
    create_image(tmp_path / "photos" / "two.jpg", (0, 255, 0))

    with run_test_backend(
        data_dir=tmp_path / "app-data",
        embedding_provider=DeterministicEmbeddingProvider(dimensions=8),
    ) as base_url:
        status = fetch_json(f"{base_url}api/status")
        assert isinstance(status, dict)
        assert status["studioApiVersion"] == STUDIO_API_VERSION
        assert status["studioVersion"] == "0.1.0"
        assert status["totalAssets"] == 0

        imported = post_json(
            f"{base_url}api/import/folder",
            {"path": str(tmp_path / "photos")},
        )
        assert isinstance(imported, dict)
        assert imported["ok"] is True

        assets = fetch_json(f"{base_url}api/assets?limit=1&offset=0")
        assert isinstance(assets, dict)
        assert assets["total"] == 2
        assert assets["limit"] == 1
        assert assets["offset"] == 0
        assert len(assets["assets"]) == 1
        asset = assets["assets"][0]
        assert isinstance(asset, dict)
        assert isinstance(asset["id"], str)
        assert asset["thumbnailUrl"].startswith("/api/thumbnails/")
        assert asset["highResThumbnailUrl"].startswith(
            "/api/high-res-thumbnails/",
        )
        assert asset["fullUrl"].startswith("/api/files/")
        assert len(asset["position"]) == 3
        with urllib.request.urlopen(  # noqa: S310
            f"{base_url}{asset['highResThumbnailUrl'].lstrip('/')}",
            timeout=5,
        ) as response:
            assert response.read(2) == b"\xff\xd8"

        atlas = fetch_json(f"{base_url}api/atlas/index.json")
        assert isinstance(atlas, dict)
        assert atlas["total"] == 2
        assert len(atlas["entries"]) == 2
        assert atlas["pages"][0]["url"].startswith("/api/atlas/pages/")

        texture_array = fetch_json(
            f"{base_url}api/texture-array/index.json?thumbSize=64&layersPerPage=4",
        )
        assert isinstance(texture_array, dict)
        assert texture_array["total"] == 2
        assert texture_array["thumbSize"] == 64
        assert texture_array["layersPerPage"] == 4
        assert len(texture_array["entries"]) == 2
        assert texture_array["pages"][0]["url"].startswith(
            "/api/texture-array/pages/",
        )
