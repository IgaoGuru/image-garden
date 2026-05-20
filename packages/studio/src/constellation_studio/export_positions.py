"""Export positions from a Studio runtime index back into constellation.json."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import cast

from constellation_studio.index_store import IndexStore
from constellation_studio.indexing import (
    default_indexing_paths,
    studio_source_id,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the export-positions CLI parser."""
    parser = argparse.ArgumentParser(
        prog="image-garden-export-positions",
        description=(
            "Export positions from a Studio runtime index back into a "
            "constellation.json BYO dataset."
        ),
    )
    parser.add_argument(
        "dataset",
        type=Path,
        help="Path to the original constellation.json (will be overwritten).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".image-garden-backend"),
        help="Runtime data directory with SQLite index.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Export runtime positions into the constellation.json dataset."""
    paths = default_indexing_paths(Path(args.data_dir))
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    dataset_path = Path(args.dataset).expanduser().resolve()
    data = read_raw_constellation_json(dataset_path)
    images = constellation_images(data, dataset_path)
    positions = store.asset_positions_for_source(
        source_type="studioDataset",
        source_id=studio_source_id(dataset_path),
    )

    updated = 0
    for image in images:
        image_id = image.get("id")
        if not isinstance(image_id, str):
            continue
        pos = positions.get(image_id)
        if pos is not None:
            image["position"] = [pos[0], pos[1], pos[2]]
            updated += 1

    dataset_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "totalImages": len(images),
                "positionedImages": updated,
                "output": str(dataset_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def read_raw_constellation_json(path: Path) -> dict[str, object]:
    """Read a raw constellation JSON object, preserving unknown fields."""
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        msg = f"expected JSON object in {path}"
        raise ValueError(msg)
    return cast("dict[str, object]", loaded)


def constellation_images(
    data: Mapping[str, object], path: Path
) -> list[MutableMapping[str, object]]:
    """Return mutable raw image objects from a constellation JSON object."""
    images_obj = data.get("images")
    if not isinstance(images_obj, list):
        msg = f"expected images list in {path}"
        raise ValueError(msg)
    images: list[MutableMapping[str, object]] = []
    for index, image_obj in enumerate(images_obj):
        if not isinstance(image_obj, dict):
            msg = f"expected image object at index {index} in {path}"
            raise ValueError(msg)
        images.append(cast("MutableMapping[str, object]", image_obj))
    return images


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
