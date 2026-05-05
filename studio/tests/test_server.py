from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from constellation_studio.schema import (
    EmbeddedImage,
    write_constellation_json,
    write_studio_manifest,
)
from constellation_studio.server import (
    find_viewer_entry_file,
    resolve_cli_paths,
    resolve_route_path,
    run_test_server,
    viewer_entry_module,
)


def create_image(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), (10, 20, 30)).save(path)
    return path.read_bytes()


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
        return response.read()


def test_resolve_route_path_rejects_traversal(tmp_path: Path) -> None:
    assert (
        resolve_route_path(tmp_path, "nested/image.jpg")
        == (tmp_path / "nested" / "image.jpg").resolve()
    )
    assert resolve_route_path(tmp_path, "../secret.txt") is None
    assert resolve_route_path(tmp_path, "%2E%2E/secret.txt") is None


def test_resolve_cli_paths_uses_embed_manifest(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    data_path = tmp_path / "constellation.json"
    write_constellation_json(data_path, [])
    write_studio_manifest(
        data_path=data_path,
        image_root=photos,
        url_prefix="assets",
    )

    assert resolve_cli_paths([data_path]) == (
        photos.resolve(),
        data_path.resolve(),
        "/assets/",
    )
    assert resolve_cli_paths([data_path], url_prefix="thumbs") == (
        photos.resolve(),
        data_path.resolve(),
        "/thumbs/",
    )


def test_viewer_entry_module_detects_and_reexports_built_viewer(
    tmp_path: Path,
) -> None:
    viewer_dist = tmp_path / "dist"
    viewer_dist.mkdir()
    entry = viewer_dist / "constellation-viewer.es.js"
    entry.write_text("export function mount() {}\n", encoding="utf-8")

    detected = find_viewer_entry_file(viewer_dist)

    assert detected == entry
    assert viewer_entry_module(viewer_dist, entry) == (
        'export * from "/viewer/constellation-viewer.es.js";\n'
    )


def test_server_serves_custom_image_prefix(tmp_path: Path) -> None:
    image_bytes = create_image(tmp_path / "photos" / "hello world.jpg")
    data_path = tmp_path / "constellation.json"
    write_constellation_json(
        data_path,
        [
            EmbeddedImage(
                id="hello world.jpg",
                url="/assets/hello%20world.jpg",
                embedding=(1.0,),
            )
        ],
    )

    with run_test_server(
        image_root=tmp_path / "photos",
        data_path=data_path,
        image_url_prefix="assets",
    ) as url:
        served_image = fetch(f"{url}assets/hello%20world.jpg")
        assert served_image == image_bytes


def test_server_serves_viewer_entry_module(tmp_path: Path) -> None:
    create_image(tmp_path / "photos" / "hello.jpg")
    data_path = tmp_path / "constellation.json"
    viewer_dist = tmp_path / "viewer-dist"
    viewer_dist.mkdir()
    (viewer_dist / "viewer.mjs").write_text(
        "export function mount() {}\n",
        encoding="utf-8",
    )
    write_constellation_json(
        data_path,
        [
            EmbeddedImage(
                id="hello.jpg",
                url="/images/hello.jpg",
                embedding=(1.0,),
            )
        ],
    )

    with run_test_server(
        image_root=tmp_path / "photos",
        data_path=data_path,
        viewer_dist=viewer_dist,
    ) as url:
        module = fetch(f"{url}viewer-entry.js")
        assert module == b'export * from "/viewer/viewer.mjs";\n'
        assert (
            fetch(f"{url}viewer/viewer.mjs") == b"export function mount() {}\n"
        )


def test_server_serves_index_json_and_images(tmp_path: Path) -> None:
    image_bytes = create_image(tmp_path / "photos" / "hello world.jpg")
    data_path = tmp_path / "constellation.json"
    write_constellation_json(
        data_path,
        [
            EmbeddedImage(
                id="hello world.jpg",
                url="/images/hello%20world.jpg",
                embedding=(1.0, 2.0, 3.0),
            )
        ],
    )

    with run_test_server(
        image_root=tmp_path / "photos", data_path=data_path
    ) as url:
        index = fetch(url)
        assert b"Constellation Studio" in index

        data = json.loads(fetch(f"{url}data.json"))
        assert data["images"][0]["embedding"] == [1.0, 2.0, 3.0]

        served_image = fetch(f"{url}images/hello%20world.jpg")
        assert served_image == image_bytes

        try:
            fetch(f"{url}images/../constellation.json")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:  # pragma: no cover
            raise AssertionError("traversal request unexpectedly succeeded")
