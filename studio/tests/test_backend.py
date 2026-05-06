from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

from constellation_studio.backend import run_test_backend
from constellation_studio.embedding_providers import (
    DeterministicEmbeddingProvider,
)
from constellation_studio.index_store import IndexStore
from constellation_studio.indexing import (
    default_indexing_paths,
    import_folder,
    import_studio_dataset,
)
from constellation_studio.schema import (
    EmbeddedImage,
    write_constellation_json,
    write_studio_manifest,
)
from constellation_studio.source_adapters import FolderSourceAdapter


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


def fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
        return response.read()


def fetch_text(url: str) -> str:
    return fetch_bytes(url).decode("utf-8")


def test_folder_source_adapter_scans_images(tmp_path: Path) -> None:
    create_image(tmp_path / "photos" / "nested" / "one.jpg", (255, 0, 0))

    adapter = FolderSourceAdapter(tmp_path / "photos")
    assets = list(adapter.scan())

    assert len(assets) == 1
    assert assets[0].source_type == "folder"
    assert assets[0].source_asset_id == "nested/one.jpg"
    assert assets[0].width == 8
    assert assets[0].height == 6
    assert assets[0].media_type == "image"
    assert assets[0].metadata["sourcePath"]
    assert adapter.source_id.startswith("folder:")


def test_import_folder_requires_embedding_provider(
    tmp_path: Path,
) -> None:
    create_image(tmp_path / "photos" / "one.jpg", (255, 0, 0))
    paths = default_indexing_paths(tmp_path / "data")
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)

    with pytest.raises(ValueError, match="No embedding engine configured"):
        import_folder(
            tmp_path / "photos",
            store=store,
            asset_root=paths.asset_root,
        )

    assert store.count_assets() == 0


def test_import_folder_can_use_embedding_provider_for_layout(
    tmp_path: Path,
) -> None:
    create_image(tmp_path / "photos" / "one.jpg", (255, 0, 0))
    create_image(tmp_path / "photos" / "two.jpg", (0, 255, 0))
    paths = default_indexing_paths(tmp_path / "data")
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)

    result = import_folder(
        tmp_path / "photos",
        store=store,
        asset_root=paths.asset_root,
        embedding_provider=DeterministicEmbeddingProvider(dimensions=8),
        batch_size=1,
    )

    assert result.imported == 2
    status = store.status()
    assert status.get("jobPhase") == "ready"
    assert status.get("embeddingEngine") == "deterministic/8"
    assets = store.list_assets(limit=10, offset=0)
    assert len(assets) == 2
    assert {asset["position"] for asset in assets} == {
        (-120.0, 0.0, 0.0),
        (120.0, 0.0, 0.0),
    }


def test_import_studio_dataset_persists_runtime_assets(
    tmp_path: Path,
) -> None:
    create_image(
        tmp_path / "studio-assets" / "images" / "one.jpg", (255, 0, 0)
    )
    create_image(
        tmp_path / "studio-assets" / "thumbs" / "one.jpg", (0, 255, 0)
    )
    data_path = tmp_path / "constellation.json"
    write_constellation_json(
        data_path,
        [
            EmbeddedImage(
                id="studio-one",
                url="/assets/images/one.jpg",
                thumbnail_url="/assets/thumbs/one.jpg",
                embedding=(1.0, 2.0, 3.0),
                width=8,
                height=6,
                metadata={"sourcePath": "/original/one.jpg"},
            )
        ],
    )
    write_studio_manifest(
        data_path=data_path,
        image_root=tmp_path / "studio-assets",
        url_prefix="/assets/",
    )
    paths = default_indexing_paths(tmp_path / "data")
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)

    result = import_studio_dataset(data_path, store=store)

    assert result.imported == 1
    assert result.source_type == "studioDataset"
    asset = store.list_assets(limit=10, offset=0)[0]
    assert asset["id"] == "studio-one"
    assert asset["thumbnailUrl"] == "/api/thumbnails/studio-one"
    assert asset.get("fullUrl") == "/api/files/studio-one"
    assert asset["position"] == (0.0, 0.0, 0.0)
    metadata = asset.get("metadata")
    assert metadata is not None
    assert metadata["datasetPath"] == str(data_path.resolve())
    assert metadata["sourcePath"] == "/original/one.jpg"


def test_backend_background_folder_import_reports_progress(
    tmp_path: Path,
) -> None:
    create_image(tmp_path / "photos" / "one.jpg", (255, 0, 0))

    with run_test_backend(
        data_dir=tmp_path / "app-data",
        embedding_provider=DeterministicEmbeddingProvider(dimensions=8),
    ) as base_url:
        started = post_json(
            f"{base_url}api/import/folder",
            {"path": str(tmp_path / "photos"), "background": True},
        )
        assert isinstance(started, dict)
        assert started["ok"] is True
        assert started["started"] is True

        deadline = time.time() + 5
        status: object = {}
        while time.time() < deadline:
            status = fetch_json(f"{base_url}api/status")
            assert isinstance(status, dict)
            if status["totalAssets"] == 1:
                break
            time.sleep(0.05)
        assert isinstance(status, dict)
        assert status["totalAssets"] == 1
        assert status.get("jobPhase") == "ready"


def test_backend_import_studio_route(tmp_path: Path) -> None:
    create_image(
        tmp_path / "studio-assets" / "images" / "one.jpg", (255, 0, 0)
    )
    create_image(
        tmp_path / "studio-assets" / "thumbs" / "one.jpg", (0, 255, 0)
    )
    data_path = tmp_path / "constellation.json"
    write_constellation_json(
        data_path,
        [
            EmbeddedImage(
                id="studio-one",
                url="/assets/images/one.jpg",
                thumbnail_url="/assets/thumbs/one.jpg",
                embedding=(1.0,),
            )
        ],
    )
    manifest_path = write_studio_manifest(
        data_path=data_path,
        image_root=tmp_path / "studio-assets",
        url_prefix="/assets/",
    )

    with run_test_backend(data_dir=tmp_path / "app-data") as base_url:
        imported = post_json(
            f"{base_url}api/import/studio",
            {"path": str(manifest_path)},
        )
        assert isinstance(imported, dict)
        assert imported["ok"] is True
        assert imported["imported"] == 1
        assert imported["sourceType"] == "studioDataset"

        listed = fetch_json(f"{base_url}api/assets")
        assert isinstance(listed, dict)
        assert listed["total"] == 1
        asset = listed["assets"][0]
        assert asset["id"] == "studio-one"
        assert fetch_bytes(f"{base_url}api/files/studio-one").startswith(
            b"\xff\xd8"
        )


def test_backend_import_folder_and_serves_local_api(tmp_path: Path) -> None:
    create_image(tmp_path / "photos" / "one.jpg", (255, 0, 0))
    create_image(tmp_path / "photos" / "two.png", (0, 255, 0))

    with run_test_backend(
        data_dir=tmp_path / "app-data",
        embedding_provider=DeterministicEmbeddingProvider(dimensions=8),
    ) as base_url:
        status = fetch_json(f"{base_url}api/status")
        assert isinstance(status, dict)
        assert status["totalAssets"] == 0

        sources = fetch_json(f"{base_url}api/sources")
        assert isinstance(sources, dict)
        source_types = {source["type"] for source in sources["sources"]}
        assert source_types == {"folder", "studioDataset"}
        assert all(source["enabled"] is True for source in sources["sources"])

        index_html = fetch_text(base_url)
        assert "Bring your own photos" in index_html
        assert "Constellation Studio dataset" in index_html
        assert "cloud connector" not in index_html

        imported = post_json(
            f"{base_url}api/import/folder",
            {"path": str(tmp_path / "photos")},
        )
        assert isinstance(imported, dict)
        assert imported["ok"] is True
        assert imported["imported"] == 2

        listed = fetch_json(f"{base_url}api/assets?limit=1&offset=0")
        assert isinstance(listed, dict)
        assert listed["total"] == 2
        assert len(listed["assets"]) == 1
        asset = listed["assets"][0]
        assert asset["position"]
        assert "embedding" not in asset

        fetched = fetch_json(f"{base_url}api/assets/{asset['id']}")
        assert fetched == asset

        nearby = fetch_json(
            f"{base_url}api/assets/near?x=0&y=0&z=0&radius=1000",
        )
        assert isinstance(nearby, dict)
        assert nearby["total"] == 2

        thumbnail_bytes = fetch_bytes(
            f"{base_url}api/thumbnails/{asset['id']}"
        )
        file_bytes = fetch_bytes(f"{base_url}api/files/{asset['id']}")
        assert thumbnail_bytes.startswith(b"\xff\xd8")
        assert file_bytes.startswith(b"\xff\xd8")

        try:
            fetch_json(f"{base_url}api/assets/missing")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:  # pragma: no cover
            raise AssertionError("missing asset unexpectedly resolved")
