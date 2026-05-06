"""SQLite storage for indexed runtime assets."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import NotRequired, TypedDict, cast

Vec3 = tuple[float, float, float]


class RuntimeAsset(TypedDict):
    """Positioned runtime asset consumed by the viewer/frontend."""

    id: str
    thumbnailUrl: str
    position: tuple[float, float, float]
    fullUrl: NotRequired[str]
    width: NotRequired[int]
    height: NotRequired[int]
    metadata: NotRequired[dict[str, object]]


class IndexStatus(TypedDict):
    """Local backend/indexer status response."""

    state: str
    paused: bool
    totalAssets: int
    importedAssets: int
    dbPath: str
    assetRoot: str
    jobPhase: NotRequired[str]
    jobCompleted: NotRequired[int]
    jobTotal: NotRequired[int]
    jobMessage: NotRequired[str]
    embeddingEngine: NotRequired[str]
    lastImportPath: NotRequired[str]


@dataclass(frozen=True, slots=True)
class StoredRuntimeAsset:
    """Runtime asset plus backend-private file/source fields."""

    id: str
    thumbnail_path: Path
    file_path: Path
    position: Vec3
    width: int | None = None
    height: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    source_type: str = "folder"
    source_id: str = ""
    source_asset_id: str = ""
    stable_key: str = ""
    creation_date: str | None = None
    media_type: str = "image"


class IndexStore:
    """Small SQLite wrapper for positioned runtime assets."""

    db_path: Path
    asset_root: Path

    def __init__(self, db_path: Path, *, asset_root: Path) -> None:
        self.db_path = db_path.expanduser().resolve()
        self.asset_root = asset_root.expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.asset_root.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        """Create or migrate the prototype schema."""
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    thumbnail_path TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    width INTEGER,
                    height INTEGER,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    z REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_asset_id TEXT NOT NULL,
                    stable_key TEXT NOT NULL,
                    creation_date TEXT,
                    media_type TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_assets_source
                    ON assets(source_type, source_id, source_asset_id);
                CREATE TABLE IF NOT EXISTS status (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """,
            )
            self._set_status_value(connection, "state", "idle")
            self._set_status_value(connection, "paused", "false")
            self._set_status_value(connection, "jobPhase", "idle")

    def upsert_asset(self, asset: StoredRuntimeAsset) -> None:
        """Insert or update a runtime asset."""
        metadata_json = json.dumps(
            asset.metadata,
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO assets (
                    id, thumbnail_path, file_path, width, height, x, y, z,
                    metadata_json, source_type, source_id, source_asset_id,
                    stable_key, creation_date, media_type, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    thumbnail_path = excluded.thumbnail_path,
                    file_path = excluded.file_path,
                    width = excluded.width,
                    height = excluded.height,
                    x = excluded.x,
                    y = excluded.y,
                    z = excluded.z,
                    metadata_json = excluded.metadata_json,
                    source_type = excluded.source_type,
                    source_id = excluded.source_id,
                    source_asset_id = excluded.source_asset_id,
                    stable_key = excluded.stable_key,
                    creation_date = excluded.creation_date,
                    media_type = excluded.media_type,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    asset.id,
                    str(asset.thumbnail_path),
                    str(asset.file_path),
                    asset.width,
                    asset.height,
                    asset.position[0],
                    asset.position[1],
                    asset.position[2],
                    metadata_json,
                    asset.source_type,
                    asset.source_id,
                    asset.source_asset_id,
                    asset.stable_key,
                    asset.creation_date,
                    asset.media_type,
                ),
            )

    def set_index_state(self, state: str) -> None:
        """Persist a simple index state string."""
        with self._connect() as connection:
            self._set_status_value(connection, "state", state)

    def set_paused(self, *, paused: bool) -> None:
        """Persist pause state for the local API."""
        with self._connect() as connection:
            self._set_status_value(
                connection,
                "paused",
                "true" if paused else "false",
            )
            self._set_status_value(
                connection,
                "state",
                "paused" if paused else "idle",
            )

    def set_job_progress(
        self,
        *,
        phase: str,
        completed: int,
        total: int,
        message: str = "",
    ) -> None:
        """Persist coarse progress for the current local job."""
        with self._connect() as connection:
            self._set_status_value(connection, "jobPhase", phase)
            self._set_status_value(connection, "jobCompleted", str(completed))
            self._set_status_value(connection, "jobTotal", str(total))
            self._set_status_value(connection, "jobMessage", message)

    def set_embedding_engine(self, engine: str) -> None:
        """Persist the selected embedding engine for status displays."""
        with self._connect() as connection:
            self._set_status_value(connection, "embeddingEngine", engine)

    def set_last_import_path(self, path: Path) -> None:
        """Persist the last imported folder path."""
        with self._connect() as connection:
            self._set_status_value(
                connection,
                "lastImportPath",
                str(path.expanduser().resolve()),
            )

    def status(self) -> IndexStatus:
        """Return API status."""
        values = self._status_values()
        total = self.count_assets()
        status: IndexStatus = {
            "state": values.get("state", "idle"),
            "paused": values.get("paused", "false") == "true",
            "totalAssets": total,
            "importedAssets": total,
            "dbPath": str(self.db_path),
            "assetRoot": str(self.asset_root),
        }
        job_phase = values.get("jobPhase")
        if job_phase is not None:
            status["jobPhase"] = job_phase
        job_completed = optional_status_int(values, "jobCompleted")
        if job_completed is not None:
            status["jobCompleted"] = job_completed
        job_total = optional_status_int(values, "jobTotal")
        if job_total is not None:
            status["jobTotal"] = job_total
        job_message = values.get("jobMessage")
        if job_message:
            status["jobMessage"] = job_message
        embedding_engine = values.get("embeddingEngine")
        if embedding_engine:
            status["embeddingEngine"] = embedding_engine
        last_import_path = values.get("lastImportPath")
        if last_import_path is not None:
            status["lastImportPath"] = last_import_path
        return status

    def clear_assets(self) -> None:
        """Clear indexed assets and reset import progress/status."""
        with self._connect() as connection:
            connection.execute("DELETE FROM assets")
            self._set_status_value(connection, "state", "idle")
            self._set_status_value(connection, "paused", "false")
            self._set_status_value(connection, "jobPhase", "idle")
            self._set_status_value(connection, "jobCompleted", "0")
            self._set_status_value(connection, "jobTotal", "0")
            self._set_status_value(connection, "jobMessage", "")
            self._set_status_value(connection, "lastImportPath", "")

    def count_assets(self) -> int:
        """Return the number of indexed runtime assets."""
        with self._connect() as connection:
            row = cast(
                "sqlite3.Row | None",
                connection.execute("SELECT COUNT(*) FROM assets").fetchone(),
            )
        if row is None:
            return 0
        return int(str(cast("object", row[0])))

    def list_assets(self, *, limit: int, offset: int) -> list[RuntimeAsset]:
        """Return positioned runtime assets for the API."""
        with self._connect() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    """
                    SELECT * FROM assets
                    ORDER BY id
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall(),
            )
        return [runtime_asset_from_row(row) for row in rows]

    def nearby_assets(
        self,
        *,
        point: Vec3,
        radius: float,
        limit: int,
    ) -> list[RuntimeAsset]:
        """Return assets within a simple Euclidean radius."""
        x, y, z = point
        radius2 = radius * radius
        with self._connect() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    """
                    SELECT * FROM (
                        SELECT *,
                            ((x - ?) * (x - ?) +
                             (y - ?) * (y - ?) +
                             (z - ?) * (z - ?)) AS distance2
                        FROM assets
                    )
                    WHERE distance2 <= ?
                    ORDER BY distance2 ASC
                    LIMIT ?
                    """,
                    (x, x, y, y, z, z, radius2, limit),
                ).fetchall(),
            )
        return [runtime_asset_from_row(row) for row in rows]

    def get_asset(self, asset_id: str) -> RuntimeAsset | None:
        """Return one runtime asset by id."""
        row = self._asset_row(asset_id)
        if row is None:
            return None
        return runtime_asset_from_row(row)

    def asset_file_path(self, asset_id: str) -> Path | None:
        """Return the canonical local file path for an asset id."""
        row = self._asset_row(asset_id)
        if row is None:
            return None
        return Path(row_str(row, "file_path"))

    def asset_thumbnail_path(self, asset_id: str) -> Path | None:
        """Return the local thumbnail path for an asset id."""
        row = self._asset_row(asset_id)
        if row is None:
            return None
        return Path(row_str(row, "thumbnail_path"))

    def _asset_row(self, asset_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT * FROM assets WHERE id = ?",
                    (asset_id,),
                ).fetchone(),
            )

    def _status_values(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute("SELECT key, value FROM status").fetchall(),
            )
        values: dict[str, str] = {}
        for row in rows:
            key = row_str(row, "key")
            value = row_str(row, "value")
            values[key] = value
        return values

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _set_status_value(
        self,
        connection: sqlite3.Connection,
        key: str,
        value: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO status(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def runtime_asset_from_row(row: sqlite3.Row) -> RuntimeAsset:
    """Convert a SQLite row to the agreed runtime asset contract."""
    asset_id = row_str(row, "id")
    metadata = metadata_from_json(row_str(row, "metadata_json"))
    creation_date = optional_str(row, "creation_date")
    media_type = optional_str(row, "media_type")
    if creation_date is not None:
        metadata.setdefault("creationDate", creation_date)
    if media_type is not None:
        metadata.setdefault("mediaType", media_type)
    asset: RuntimeAsset = {
        "id": asset_id,
        "thumbnailUrl": f"/api/thumbnails/{asset_id}",
        "fullUrl": f"/api/files/{asset_id}",
        "position": (
            row_float(row, "x"),
            row_float(row, "y"),
            row_float(row, "z"),
        ),
    }
    width = optional_int(row, "width")
    height = optional_int(row, "height")
    if width is not None:
        asset["width"] = width
    if height is not None:
        asset["height"] = height
    if metadata:
        asset["metadata"] = metadata
    return asset


def metadata_from_json(payload: str) -> dict[str, object]:
    """Decode metadata JSON as a string-keyed object."""
    loaded = cast("object", json.loads(payload))
    if not isinstance(loaded, Mapping):
        return {}
    typed = cast("Mapping[object, object]", loaded)
    return {
        str(key): value for key, value in typed.items() if isinstance(key, str)
    }


def row_value(row: sqlite3.Row, key: str) -> object:
    """Return a row value as object for local conversion helpers."""
    return cast("object", row[key])


def row_str(row: sqlite3.Row, key: str) -> str:
    """Return a row value as a string."""
    return str(row_value(row, key))


def row_float(row: sqlite3.Row, key: str) -> float:
    """Return a row value as a float."""
    return float(row_str(row, key))


def optional_str(row: sqlite3.Row, key: str) -> str | None:
    """Return an optional string column."""
    value = row_value(row, key)
    if value is None:
        return None
    return str(value)


def optional_int(row: sqlite3.Row, key: str) -> int | None:
    """Return an optional int column."""
    value = row_value(row, key)
    if value is None:
        return None
    return int(str(value))


def optional_status_int(values: Mapping[str, str], key: str) -> int | None:
    """Return an optional integer status value."""
    raw = values.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def iter_assets(store: IndexStore) -> Iterator[RuntimeAsset]:
    """Iterate every asset in stable order."""
    offset = 0
    page_size = 500
    while True:
        page = store.list_assets(limit=page_size, offset=offset)
        if not page:
            return
        yield from page
        offset += len(page)
