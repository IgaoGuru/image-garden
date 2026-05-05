from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from constellation_studio.embed import EmbedOptions, embed_directory
from constellation_studio.images import (
    discover_image_files,
    image_url,
    quote_path,
)
from constellation_studio.schema import (
    read_studio_manifest,
    write_constellation_json,
    write_studio_manifest,
)


class FakeEmbedder:
    def embed_images(self, paths: Sequence[Path]) -> list[tuple[float, ...]]:
        return [
            (float(index), float(path.stat().st_size))
            for index, path in enumerate(paths)
        ]


class FailingEmbedder:
    def embed_images(self, paths: Sequence[Path]) -> list[tuple[float, ...]]:
        if any(path.name == "bad.jpg" for path in paths):
            msg = "bad test image"
            raise RuntimeError(msg)
        return [(float(len(path.name)),) for path in paths]


def create_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), color).save(path)


def test_discover_image_files_is_recursive_and_sorted(tmp_path: Path) -> None:
    create_image(tmp_path / "b.jpg", (255, 0, 0))
    create_image(tmp_path / "nested" / "a.png", (0, 255, 0))
    (tmp_path / "ignore.txt").write_text("not an image", encoding="utf-8")

    discovered = discover_image_files(tmp_path)

    assert [path.relative_to(tmp_path).as_posix() for path in discovered] == [
        "b.jpg",
        "nested/a.png",
    ]


def test_image_url_quotes_path_segments(tmp_path: Path) -> None:
    path = tmp_path / "space dir" / "é image.jpg"
    create_image(path, (0, 0, 255))

    assert (
        quote_path("space dir/é image.jpg") == "space%20dir/%C3%A9%20image.jpg"
    )
    assert image_url(path, tmp_path, "/images") == (
        "/images/space%20dir/%C3%A9%20image.jpg"
    )


def test_embed_directory_writes_viewer_contract(tmp_path: Path) -> None:
    create_image(tmp_path / "one.jpg", (255, 0, 0))
    create_image(tmp_path / "two.png", (0, 255, 0))
    progress_events: list[tuple[int, int]] = []

    embedded = embed_directory(
        tmp_path,
        embedder=FakeEmbedder(),
        options=EmbedOptions(
            batch_size=1,
            progress=lambda completed, total: progress_events.append(
                (completed, total)
            ),
        ),
    )
    output = tmp_path / "constellation.json"
    write_constellation_json(output, embedded)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert list(payload) == ["images"]
    assert [image["id"] for image in payload["images"]] == [
        "one.jpg",
        "two.png",
    ]
    assert payload["images"][0]["url"] == "/images/one.jpg"
    assert len(payload["images"][0]["embedding"]) == 2
    assert progress_events == [(1, 2), (2, 2)]

    manifest_path = write_studio_manifest(
        data_path=output,
        image_root=tmp_path,
        url_prefix="/images/",
    )
    manifest = read_studio_manifest(output)
    assert manifest_path.name == "constellation.studio.json"
    assert manifest["imageRoot"] == str(tmp_path.resolve())
    assert manifest["dataJson"] == str(output.resolve())


def test_embed_directory_can_skip_bad_images_without_misaligning_ids(
    tmp_path: Path,
) -> None:
    create_image(tmp_path / "a.jpg", (255, 0, 0))
    create_image(tmp_path / "bad.jpg", (0, 255, 0))
    create_image(tmp_path / "c.jpg", (0, 0, 255))
    warnings: list[str] = []

    embedded = embed_directory(
        tmp_path,
        embedder=FailingEmbedder(),
        options=EmbedOptions(
            batch_size=3,
            skip_errors=True,
            warn=warnings.append,
        ),
    )

    assert [image.id for image in embedded] == ["a.jpg", "c.jpg"]
    assert [image.embedding for image in embedded] == [(5.0,), (5.0,)]
    assert any("bad.jpg" in warning for warning in warnings)
    assert warnings[-1] == "Skipped 1 image(s) that failed to embed."
