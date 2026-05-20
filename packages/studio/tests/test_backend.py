# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import json
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from constellation_studio.backend import (
    build_texture_array_index,
    run_test_backend,
)
from constellation_studio.embedding_providers import (
    CLIP_IMAGE_MEAN,
    CLIP_IMAGE_STD,
    DeterministicEmbeddingProvider,
    read_onnx_preprocess_options,
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


def create_image(
    path: Path,
    color: tuple[int, int, int],
    *,
    size: tuple[int, int] = (8, 6),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


class BatchLimitedEmbedder:
    """Fake provider that simulates engines rejecting oversized batches."""

    cache_namespace = "batch-limited"

    def __init__(self, max_batch: int) -> None:
        self.max_batch = max_batch
        self.calls: list[int] = []

    def embed_images(self, paths: Sequence[Path]) -> list[tuple[float, ...]]:
        self.calls.append(len(paths))
        if len(paths) > self.max_batch:
            msg = "batch too large"
            raise RuntimeError(msg)
        return [(float(path.stat().st_size),) for path in paths]


def create_playview_dist(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "index.html").write_text(
        '<!doctype html><div id="viewer"></div><script type="module" '
        'src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    assets = path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('playview')\n")
    audio = path / "audio"
    audio.mkdir()
    (audio / "wind-ambience.mp3").write_bytes(b"fake mp3")
    return path


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


def test_onnx_preprocess_options_use_clip_defaults_without_config(
    tmp_path: Path,
) -> None:
    options = read_onnx_preprocess_options(
        tmp_path / "vision_model.onnx",
        default_image_size=224,
    )

    assert options.image_size == 224
    assert options.mean == CLIP_IMAGE_MEAN
    assert options.std == CLIP_IMAGE_STD


def test_onnx_preprocess_options_read_mobileclip_config(
    tmp_path: Path,
) -> None:
    (tmp_path / "onnx").mkdir()
    (tmp_path / "preprocessor_config.json").write_text(
        json.dumps(
            {
                "crop_size": {"height": 256, "width": 256},
                "do_normalize": False,
                "do_rescale": True,
                "image_mean": [0.1, 0.2, 0.3],
                "image_std": [0.4, 0.5, 0.6],
                "rescale_factor": 1.0 / 255.0,
            },
        ),
        encoding="utf-8",
    )

    options = read_onnx_preprocess_options(
        tmp_path / "onnx" / "vision_model.onnx",
        default_image_size=224,
    )

    assert options.image_size == 256
    assert options.mean == (0.0, 0.0, 0.0)
    assert options.std == (1.0, 1.0, 1.0)


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


def test_index_store_status_uses_memory_snapshot_when_db_unavailable(
    tmp_path: Path,
) -> None:
    paths = default_indexing_paths(tmp_path / "data")
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    store.set_job_progress(
        phase="sanitizing",
        completed=12,
        total=30,
        message="Preparing local JPEG assets",
    )

    shutil.rmtree(paths.data_dir)
    status = store.status()

    assert status["state"] == "idle"
    assert status["jobPhase"] == "sanitizing"
    assert status["jobCompleted"] == 12
    assert status["jobTotal"] == 30
    assert status["jobMessage"] == "Preparing local JPEG assets"


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


def test_import_folder_splits_embedding_batches_on_runtime_failure(
    tmp_path: Path,
) -> None:
    for index, color in enumerate(
        [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)],
    ):
        create_image(tmp_path / "photos" / f"{index}.jpg", color)
    paths = default_indexing_paths(tmp_path / "data")
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    embedder = BatchLimitedEmbedder(max_batch=2)

    result = import_folder(
        tmp_path / "photos",
        store=store,
        asset_root=paths.asset_root,
        embedding_provider=embedder,
        batch_size=4,
    )

    assert result.imported == 4
    assert store.count_assets() == 4
    assert embedder.calls == [4, 2, 2]


def test_texture_array_uses_studio_dataset_thumbnail_paths(
    tmp_path: Path,
) -> None:
    create_image(tmp_path / "images" / "one.jpg", (255, 0, 0), size=(24, 24))
    create_image(tmp_path / "thumbs" / "one.jpg", (250, 10, 20), size=(12, 12))
    data_path = tmp_path / "constellation.json"
    data_path.write_text(
        json.dumps(
            {
                "images": [
                    {
                        "id": "external-one",
                        "url": "images/one.jpg",
                        "thumbnailUrl": "thumbs/one.jpg",
                        "position": [1, 2, 3],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    paths = default_indexing_paths(tmp_path / "data")
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    _ = import_studio_dataset(data_path, store=store)

    _ = build_texture_array_index(
        store,
        paths.asset_root,
        thumb_size=16,
        layers_per_page=4,
    )

    page = Image.open(
        paths.asset_root / "texture-array" / "thumb16-layers4" / "page-0.jpg"
    ).convert("RGB")
    stat = ImageStat.Stat(page.crop((0, 0, 16, 16)))
    assert stat.mean[0] > 150


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


def test_import_studio_dataset_computes_missing_embeddings(
    tmp_path: Path,
) -> None:
    create_image(
        tmp_path / "studio-assets" / "images" / "one.jpg", (255, 0, 0)
    )
    create_image(
        tmp_path / "studio-assets" / "thumbs" / "one.jpg", (0, 255, 0)
    )
    data_path = tmp_path / "constellation.json"
    data_path.write_text(
        json.dumps(
            {
                "images": [
                    {
                        "id": "external-one",
                        "url": "/assets/images/one.jpg",
                        "thumbnailUrl": "/assets/thumbs/one.jpg",
                        "width": 8,
                        "height": 6,
                        "metadata": {"source": "byo"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    write_studio_manifest(
        data_path=data_path,
        image_root=tmp_path / "studio-assets",
        url_prefix="/assets/",
    )
    paths = default_indexing_paths(tmp_path / "data")
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)

    result = import_studio_dataset(
        data_path,
        store=store,
        embedding_provider=DeterministicEmbeddingProvider(dimensions=8),
        batch_size=1,
    )

    assert result.imported == 1
    status = store.status()
    assert status.get("embeddingEngine") == "deterministic/8"
    asset = store.list_assets(limit=10, offset=0)[0]
    assert asset["id"] == "external-one"
    assert asset["position"] == (0.0, 0.0, 0.0)
    metadata = asset.get("metadata")
    assert metadata is not None
    assert metadata["source"] == "byo"


def test_import_studio_dataset_requires_engine_for_image_only_dataset(
    tmp_path: Path,
) -> None:
    create_image(tmp_path / "images" / "one.jpg", (255, 0, 0))
    data_path = tmp_path / "constellation.json"
    data_path.write_text(
        json.dumps(
            {
                "images": [
                    {
                        "id": "external-one",
                        "url": "images/one.jpg",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    paths = default_indexing_paths(tmp_path / "data")
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)

    with pytest.raises(ValueError, match="require an embedding engine"):
        import_studio_dataset(data_path, store=store)


def test_backend_import_studio_route_computes_image_only_dataset(
    tmp_path: Path,
) -> None:
    create_image(tmp_path / "images" / "one.jpg", (255, 0, 0))
    data_path = tmp_path / "constellation.json"
    data_path.write_text(
        json.dumps(
            {
                "images": [
                    {
                        "id": "external-one",
                        "url": "images/one.jpg",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with run_test_backend(
        data_dir=tmp_path / "app-data",
        embedding_provider=DeterministicEmbeddingProvider(dimensions=8),
        embedding_batch_size=1,
    ) as base_url:
        imported = post_json(
            f"{base_url}api/import/studio",
            {"path": str(data_path)},
        )
        assert isinstance(imported, dict)
        assert imported["ok"] is True
        assert imported["imported"] == 1

        listed = fetch_json(f"{base_url}api/assets")
        assert isinstance(listed, dict)
        assert listed["total"] == 1
        assert listed["assets"][0]["id"] == "external-one"


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


def test_backend_serves_playview_public_audio(tmp_path: Path) -> None:
    with run_test_backend(
        data_dir=tmp_path / "app-data",
        playview_dist=create_playview_dist(tmp_path / "playview-dist"),
    ) as base_url:
        assert fetch_bytes(f"{base_url}audio/wind-ambience.mp3") == b"fake mp3"


def test_backend_serves_generated_files_without_sqlite_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_image(tmp_path / "photos" / "one.jpg", (255, 0, 0))

    with run_test_backend(
        data_dir=tmp_path / "app-data",
        embedding_provider=DeterministicEmbeddingProvider(dimensions=8),
    ) as base_url:
        imported = post_json(
            f"{base_url}api/import/folder",
            {"path": str(tmp_path / "photos")},
        )
        assert isinstance(imported, dict)
        assert imported["ok"] is True

        listed = fetch_json(f"{base_url}api/assets?limit=1")
        assert isinstance(listed, dict)
        asset = listed["assets"][0]
        asset_id = asset["id"]

        def fail_path_lookup(self: IndexStore, asset_id: str) -> Path | None:
            _ = self, asset_id
            raise sqlite3.OperationalError("database unavailable")

        monkeypatch.setattr(
            IndexStore,
            "asset_thumbnail_path",
            fail_path_lookup,
        )
        monkeypatch.setattr(IndexStore, "asset_file_path", fail_path_lookup)

        thumbnail_bytes = fetch_bytes(f"{base_url}api/thumbnails/{asset_id}")
        file_bytes = fetch_bytes(f"{base_url}api/files/{asset_id}")

        assert thumbnail_bytes.startswith(b"\xff\xd8")
        assert file_bytes.startswith(b"\xff\xd8")


def test_backend_get_store_failure_returns_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_list_assets(
        self: IndexStore,
        *,
        limit: int,
        offset: int,
    ) -> list[object]:
        _ = self, limit, offset
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(IndexStore, "list_assets", fail_list_assets)

    with run_test_backend(data_dir=tmp_path / "app-data") as base_url:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            fetch_json(f"{base_url}api/assets")

        assert exc_info.value.code == 503
        body = json.loads(exc_info.value.read().decode("utf-8"))
        assert body["ok"] is False
        assert "database unavailable" in body["error"]


def test_backend_builds_thumbnail_atlas(tmp_path: Path) -> None:
    create_image(tmp_path / "photos" / "one.jpg", (255, 0, 0))
    create_image(tmp_path / "photos" / "two.jpg", (0, 255, 0))

    with run_test_backend(
        data_dir=tmp_path / "app-data",
        embedding_provider=DeterministicEmbeddingProvider(dimensions=8),
    ) as base_url:
        imported = post_json(
            f"{base_url}api/import/folder",
            {"path": str(tmp_path / "photos")},
        )
        assert isinstance(imported, dict)
        assert imported["ok"] is True

        atlas = fetch_json(f"{base_url}api/atlas/index.json")
        assert isinstance(atlas, dict)
        assert atlas["total"] == 2
        assert atlas["pageCount"] == 1
        entries = atlas["entries"]
        assert isinstance(entries, list)
        assert len(entries) == 2
        pages = atlas["pages"]
        assert isinstance(pages, list)
        page = pages[0]
        assert isinstance(page, dict)
        page_bytes = fetch_bytes(f"{base_url}{page['url']}")
        assert page_bytes.startswith(b"\xff\xd8")


def test_backend_builds_texture_array_pages(tmp_path: Path) -> None:
    create_image(tmp_path / "photos" / "wide.jpg", (255, 0, 0), size=(80, 40))
    create_image(tmp_path / "photos" / "tall.jpg", (0, 255, 0), size=(40, 80))

    with run_test_backend(
        data_dir=tmp_path / "app-data",
        embedding_provider=DeterministicEmbeddingProvider(dimensions=8),
    ) as base_url:
        imported = post_json(
            f"{base_url}api/import/folder",
            {"path": str(tmp_path / "photos")},
        )
        assert isinstance(imported, dict)
        assert imported["ok"] is True

        index = fetch_json(
            f"{base_url}api/texture-array/index.json?thumbSize=64&layersPerPage=4"
        )
        assert isinstance(index, dict)
        assert index["total"] == 2
        assert index["pageCount"] == 1
        assert index["thumbSize"] == 64
        assert index["layersPerPage"] == 4
        entries = index["entries"]
        assert isinstance(entries, list)
        assert {entry["layer"] for entry in entries} == {0, 1}
        pages = index["pages"]
        assert isinstance(pages, list)
        page = pages[0]
        assert isinstance(page, dict)
        assert page["layers"] == 2
        page_bytes = fetch_bytes(f"{base_url}{page['url']}")
        assert page_bytes.startswith(b"\xff\xd8")


def test_backend_import_folder_and_serves_local_api(tmp_path: Path) -> None:
    create_image(tmp_path / "photos" / "one.jpg", (255, 0, 0))
    create_image(tmp_path / "photos" / "two.png", (0, 255, 0))

    with run_test_backend(
        data_dir=tmp_path / "app-data",
        playview_dist=create_playview_dist(tmp_path / "playview-dist"),
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
        assert 'id="viewer"' in index_html
        assert (
            fetch_text(f"{base_url}assets/app.js")
            == "console.log('playview')\n"
        )
        assert "build your constellation of images" not in index_html

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

        cleared = post_json(f"{base_url}api/data/clear", {})
        assert isinstance(cleared, dict)
        assert cleared["ok"] is True
        cleared_status = fetch_json(f"{base_url}api/status")
        assert isinstance(cleared_status, dict)
        assert cleared_status["totalAssets"] == 0
        listed_after_clear = fetch_json(f"{base_url}api/assets?limit=10")
        assert isinstance(listed_after_clear, dict)
        assert listed_after_clear["assets"] == []
