"""CLI for importing an Image Garden BYO dataset into Studio runtime data."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from constellation_studio.backend import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MODEL,
    DEFAULT_PRETRAINED,
)
from constellation_studio.embedding_providers import (
    create_embedding_provider,
    preflight_embedding_provider,
)
from constellation_studio.index_store import IndexStore
from constellation_studio.indexing import (
    default_indexing_paths,
    import_studio_dataset,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the BYO dataset import CLI parser."""
    parser = argparse.ArgumentParser(
        prog="image-garden-import-dataset",
        description=(
            "Import a BYO constellation.json/constellation.studio.json "
            "dataset into an Image Garden runtime index."
        ),
    )
    parser.add_argument(
        "dataset",
        type=Path,
        help="Path to constellation.json or constellation.studio.json.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".image-garden-backend"),
        help="Runtime data directory for SQLite/assets/cache.",
    )
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=None,
        help="Optional asset root override for URL-style dataset paths.",
    )
    parser.add_argument(
        "--embedding-engine",
        default="none",
        help="Embedding engine: none, openclip, or onnx.",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_MODEL,
        help=f"OpenCLIP model name (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--embedding-pretrained",
        default=DEFAULT_PRETRAINED,
        help=f"OpenCLIP pretrained tag (default: {DEFAULT_PRETRAINED}).",
    )
    parser.add_argument(
        "--embedding-device",
        default="auto",
        help="OpenCLIP device: auto, cpu, mps, cuda.",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Images per embedding batch (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--onnx-model",
        type=Path,
        default=None,
        help="Path to ONNX image encoder for --embedding-engine=onnx.",
    )
    parser.add_argument(
        "--onnx-provider",
        default="auto",
        help="ONNX Runtime provider: auto, cpu, cuda, directml, coreml.",
    )
    parser.add_argument(
        "--recompute-layout",
        action="store_true",
        help=(
            "Ignore existing position fields and rebuild positions from "
            "embeddings/selected embedding engine."
        ),
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Import one BYO dataset into an Image Garden runtime index."""
    console = Console(stderr=True, log_path=False)
    paths = default_indexing_paths(Path(args.data_dir))
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    embedding_provider = create_embedding_provider(
        engine=str(args.embedding_engine),
        model=str(args.embedding_model),
        pretrained=str(args.embedding_pretrained),
        device=str(args.embedding_device),
        onnx_model=cast("Path | None", args.onnx_model),
        onnx_provider=str(args.onnx_provider),
    )
    preflight_embedding_provider(embedding_provider)
    if embedding_provider is not None:
        store.set_embedding_engine(embedding_provider.cache_namespace)
    else:
        store.set_embedding_engine("none")

    dataset_path = Path(args.dataset)
    batch_size = int(args.embedding_batch_size)
    asset_dir = cast("Path | None", args.asset_dir)

    console.log(
        f"Importing dataset [cyan]{dataset_path}[/cyan] "
        f"engine=[cyan]{args.embedding_engine!s}[/cyan] "
        f"recomputeLayout=[cyan]{bool(args.recompute_layout)!s}[/cyan]"
    )

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("elapsed"),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        console=console,
    )
    task_id = progress.add_task("starting", total=1)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            import_studio_dataset,
            dataset_path,
            store=store,
            asset_dir=asset_dir,
            embedding_provider=embedding_provider,
            batch_size=batch_size,
            recompute_layout=bool(args.recompute_layout),
        )
        with progress:
            while not future.done():
                _update_progress_bar(store, progress, task_id)
                time.sleep(0.25)
            _update_progress_bar(store, progress, task_id)
        result = future.result()

    console.log(
        f"Imported [green]{result.imported}[/green] assets "
        f"(total [green]{result.total_assets}[/green])"
    )
    summary = {
        "ok": True,
        "imported": result.imported,
        "totalAssets": result.total_assets,
        "sourceType": result.source_type,
        "sourceId": result.source_id,
        "dataset": str(result.data_json),
        "assetRoot": str(result.image_root),
        "dbPath": str(paths.db_path),
        "runtimeAssetRoot": str(paths.asset_root),
        "embeddingEngine": store.status().get("embeddingEngine"),
        "recomputeLayout": bool(args.recompute_layout),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _update_progress_bar(
    store: IndexStore, progress: Progress, task_id: TaskID
) -> None:
    """Read store progress and update the rich progress bar."""
    try:
        status = store.status()
    except (OSError, RuntimeError):
        return

    phase = status.get("jobPhase", "")
    completed = status.get("jobCompleted", 0)
    total = status.get("jobTotal", 0)
    message = status.get("jobMessage", "")

    phase_label = {
        "importing": "reading dataset",
        "resolving": "resolving paths",
        "embedding": "generating embeddings",
        "layout": "building 3D layout",
        "indexing": "writing catalog",
    }.get(phase, phase or "preparing")

    description = f"[bold]{phase_label}[/bold]"
    short_message = _abbreviate_progress_message(message)
    if short_message:
        description += f"     {short_message}"

    if total > 0 and total != progress.tasks[task_id].total:
        progress.update(task_id, total=total)
    progress.update(
        task_id,
        completed=completed,
        description=description,
    )


def _abbreviate_progress_message(message: str) -> str:
    """Shorten verbose embedding engine names for the progress bar."""
    if not message:
        return ""
    if len(message) > 80 and "/" in message:
        parts = message.split("/")
        if len(parts) >= 2:
            return parts[0] + "/" + parts[1]
    if len(message) > 60:
        return message[:57] + "..."
    return message


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
