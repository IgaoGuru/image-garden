from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    import pytest

from constellation_studio import export_positions, import_dataset
from constellation_studio.index_store import IndexStore


def create_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 6), color).save(path)


def test_import_dataset_cli_imports_positioned_byo_dataset(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
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
                        "position": [1, 2, 3],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    exit_code = import_dataset.main(
        [
            str(data_path),
            "--data-dir",
            str(tmp_path / "runtime"),
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["ok"] is True
    assert summary["imported"] == 1
    assert summary["totalAssets"] == 1
    assert Path(summary["dbPath"]).is_file()


def test_import_dataset_cli_recomputes_existing_positions_from_embeddings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name, color in (
        ("one.jpg", (255, 0, 0)),
        ("two.jpg", (0, 255, 0)),
        ("three.jpg", (0, 0, 255)),
    ):
        create_image(tmp_path / "images" / name, color)
    data_path = tmp_path / "constellation.json"
    data_path.write_text(
        json.dumps(
            {
                "images": [
                    {
                        "id": "external-one",
                        "url": "images/one.jpg",
                        "position": [100, 0, 0],
                        "embedding": [1, 0, 0],
                    },
                    {
                        "id": "external-two",
                        "url": "images/two.jpg",
                        "position": [100, 0, 0],
                        "embedding": [0, 1, 0],
                    },
                    {
                        "id": "external-three",
                        "url": "images/three.jpg",
                        "position": [100, 0, 0],
                        "embedding": [0, 0, 1],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"

    exit_code = import_dataset.main(
        [
            str(data_path),
            "--data-dir",
            str(runtime_dir),
            "--recompute-layout",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["recomputeLayout"] is True
    store = IndexStore(
        Path(summary["dbPath"]),
        asset_root=Path(summary["runtimeAssetRoot"]),
    )
    positions = [
        asset["position"] for asset in store.list_assets(limit=10, offset=0)
    ]
    assert positions != [(100.0, 0.0, 0.0)] * 3


def test_export_positions_preserves_unknown_fields(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_image(tmp_path / "images" / "one.jpg", (255, 0, 0))
    data_path = tmp_path / "constellation.json"
    data_path.write_text(
        json.dumps(
            {
                "datasetVersion": 1,
                "images": [
                    {
                        "id": "external-one",
                        "url": "images/one.jpg",
                        "customField": "keep-me",
                        "position": [1, 2, 3],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    assert (
        import_dataset.main([str(data_path), "--data-dir", str(runtime_dir)])
        == 0
    )
    _ = capsys.readouterr()
    data_path.write_text(
        json.dumps(
            {
                "datasetVersion": 1,
                "images": [
                    {
                        "id": "external-one",
                        "url": "images/one.jpg",
                        "customField": "keep-me",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = export_positions.main(
        [str(data_path), "--data-dir", str(runtime_dir)]
    )

    assert exit_code == 0
    loaded = json.loads(data_path.read_text(encoding="utf-8"))
    assert loaded["datasetVersion"] == 1
    assert loaded["images"][0]["customField"] == "keep-me"
    assert loaded["images"][0]["position"] == [1.0, 2.0, 3.0]


def test_export_positions_does_not_use_other_dataset_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_image(tmp_path / "a" / "one.jpg", (255, 0, 0))
    create_image(tmp_path / "b" / "one.jpg", (0, 255, 0))
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(
        json.dumps(
            {
                "images": [
                    {
                        "id": "shared-id",
                        "url": "a/one.jpg",
                        "position": [1, 2, 3],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    second_path.write_text(
        json.dumps(
            {
                "images": [
                    {
                        "id": "shared-id",
                        "url": "b/one.jpg",
                        "position": [9, 9, 9],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    assert (
        import_dataset.main([str(first_path), "--data-dir", str(runtime_dir)])
        == 0
    )
    _ = capsys.readouterr()
    assert (
        import_dataset.main([str(second_path), "--data-dir", str(runtime_dir)])
        == 0
    )
    _ = capsys.readouterr()
    first_path.write_text(
        json.dumps({"images": [{"id": "shared-id", "url": "a/one.jpg"}]}),
        encoding="utf-8",
    )

    exit_code = export_positions.main(
        [str(first_path), "--data-dir", str(runtime_dir)]
    )

    assert exit_code == 0
    loaded = json.loads(first_path.read_text(encoding="utf-8"))
    assert "position" not in loaded["images"][0]


def test_import_dataset_cli_reports_missing_embedding_engine(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
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

    exit_code = import_dataset.main(
        [
            str(data_path),
            "--data-dir",
            str(tmp_path / "runtime"),
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "require an embedding engine" in captured.err
