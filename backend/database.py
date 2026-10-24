"""
database.py - Authoritative SQLite Database Manager
Solar Atlas: Space Hackathon AI for Solar Energy Potential Mapping

Handles connection pooling, PRAGMA foreign_keys enforcement, transactions,
schema initialization, and structured query execution.
"""

from __future__ import annotations
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "solar_atlas.db"
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
if not DEFAULT_SCHEMA_PATH.exists():
    DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def get_db_path(custom_path: Optional[Union[str, Path]] = None) -> Path:
    """Resolve database path, ensuring parent directories exist."""
    p = Path(custom_path) if custom_path else DEFAULT_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def get_connection(db_path: Optional[Union[str, Path]] = None) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager for SQLite connections.
    Enforces foreign key constraints on every connection and enables row_factory.
    Rolls back automatically on exception; commits on successful exit.
    """
    path = get_db_path(db_path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Foreign key enforcement is mandatory per SQLite specification
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(
    db_path: Optional[Union[str, Path]] = None,
    schema_path: Optional[Union[str, Path]] = None
) -> None:
    """
    Apply schema.sql to the database to ensure all 12 tables and indices exist.
    Safe to call multiple times (idempotent CREATE TABLE IF NOT EXISTS).
    """
    path = get_db_path(db_path)
    s_path = Path(schema_path) if schema_path else DEFAULT_SCHEMA_PATH
    if not s_path.is_file():
        raise FileNotFoundError(f"Schema file not found at {s_path}")

    with open(s_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    with get_connection(path) as conn:
        conn.executescript(schema_sql)


def execute_query(
    sql: str,
    params: Union[Tuple, List, Dict] = (),
    db_path: Optional[Union[str, Path]] = None
) -> List[Dict[str, Any]]:
    """Execute a read query and return rows as standard Python dictionaries."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def execute_scalar(
    sql: str,
    params: Union[Tuple, List, Dict] = (),
    db_path: Optional[Union[str, Path]] = None
) -> Any:
    """Execute a query returning a single scalar value."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return row[0] if row else None


# -------------------------------------------------------------------------
# Relational Helper Functions
# -------------------------------------------------------------------------

def register_data_source(
    source_id: str,
    name: str,
    provider_type: str,
    base_url: Optional[str] = None,
    auth_type: str = "none",
    contact_email: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> str:
    """Register or return an existing data source."""
    with get_connection(db_path) as conn:
        # Check if already registered by name
        cursor = conn.cursor()
        cursor.execute("SELECT source_id FROM data_sources WHERE name = ?", (name,))
        existing = cursor.fetchone()
        if existing:
            return existing["source_id"]

        conn.execute(
            """
            INSERT INTO data_sources (source_id, name, provider_type, base_url, auth_type, contact_email, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                name,
                provider_type,
                base_url,
                auth_type,
                contact_email,
                json.dumps(metadata or {})
            )
        )
        return source_id


def register_location(
    location_id: str,
    name: str,
    center_lat: float,
    center_lon: float,
    state: Optional[str] = None,
    country: str = "India",
    bounding_box: Optional[List[float]] = None,
    crs: str = "EPSG:4326",
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> str:
    """Register a geographic study area, or return existing if name+state matches."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT location_id FROM locations WHERE name = ? AND (state = ? OR (state IS NULL AND ? IS NULL)) AND country = ?",
            (name, state, state, country)
        )
        existing = cursor.fetchone()
        if existing:
            return existing["location_id"]

        conn.execute(
            """
            INSERT INTO locations (location_id, name, state, country, center_lat, center_lon, bounding_box_json, crs, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                location_id,
                name,
                state,
                country,
                center_lat,
                center_lon,
                json.dumps(bounding_box or []),
                crs,
                json.dumps(metadata or {})
            )
        )
        return location_id


def get_dataset(dataset_id: str, db_path: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]:
    """Retrieve full dataset record by dataset_id."""
    rows = execute_query("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,), db_path)
    return rows[0] if rows else None


def get_dataset_by_idempotency_key(
    key: str, db_path: Optional[Union[str, Path]] = None
) -> Optional[Dict[str, Any]]:
    """Check if an identical dataset has already been ingested."""
    rows = execute_query("SELECT * FROM datasets WHERE idempotency_key = ?", (key,), db_path)
    return rows[0] if rows else None


def insert_dataset(
    dataset_id: str,
    source_id: str,
    location_id: str,
    name: str,
    data_type: str,
    format_type: str,
    version: str = "v1.0",
    idempotency_key: Optional[str] = None,
    status: str = "registered",
    acquisition_date_start: Optional[str] = None,
    acquisition_date_end: Optional[str] = None,
    spatial_resolution_meters: Optional[float] = None,
    quality_score: float = 1.0,
    is_demonstration: int = 1,
    quarantine_reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Insert initial dataset record with status 'registered' or 'quarantined'."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO datasets (
                dataset_id, source_id, location_id, name, data_type, format, version,
                idempotency_key, status, quarantine_reason, acquisition_date_start, acquisition_date_end,
                spatial_resolution_meters, quality_score, is_demonstration, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                source_id,
                location_id,
                name,
                data_type,
                format_type,
                version,
                idempotency_key,
                status,
                quarantine_reason,
                acquisition_date_start,
                acquisition_date_end,
                spatial_resolution_meters,
                quality_score,
                is_demonstration,
                json.dumps(metadata or {})
            )
        )


def update_dataset_status(
    dataset_id: str,
    status: str,
    quarantine_reason: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Transition dataset lifecycle state."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE datasets
            SET status = ?, quarantine_reason = ?, updated_at = datetime('now', 'utc')
            WHERE dataset_id = ?
            """,
            (status, quarantine_reason, dataset_id)
        )


def insert_dataset_area(
    dataset_id: str,
    location_id: str,
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    srid: int = 4326,
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Record geographic bounding area for a dataset."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO dataset_areas (dataset_id, location_id, min_lat, max_lat, min_lon, max_lon, srid)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (dataset_id, location_id, min_lat, max_lat, min_lon, max_lon, srid)
        )


def insert_asset(
    asset_id: str,
    dataset_id: str,
    original_filename: str,
    storage_uri: str,
    storage_path: str,
    checksum_sha256: str,
    file_size_bytes: int,
    mime_type: str,
    asset_type: str,
    crs: Optional[str] = None,
    bounds: Optional[List[float]] = None,
    resolution_meters: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Record physical file asset in SQLite catalogue."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, dataset_id, original_filename, storage_uri, storage_path,
                checksum_sha256, file_size_bytes, mime_type, asset_type,
                crs, bounds_json, resolution_meters, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                dataset_id,
                original_filename,
                storage_uri,
                storage_path,
                checksum_sha256,
                file_size_bytes,
                mime_type,
                asset_type,
                crs,
                json.dumps(bounds or []),
                resolution_meters,
                json.dumps(metadata or {})
            )
        )


def get_assets_by_dataset(dataset_id: str, db_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Retrieve all physical assets belonging to a dataset."""
    return execute_query("SELECT * FROM assets WHERE dataset_id = ?", (dataset_id,), db_path)


def insert_pipeline_run(
    pipeline_run_id: str,
    pipeline_type: str,
    dataset_id: Optional[str],
    status: str,
    records_processed: int = 0,
    error_message: Optional[str] = None,
    step_details: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Record start or status of a pipeline run."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs (
                pipeline_run_id, pipeline_type, dataset_id, status, records_processed,
                error_message, step_details_json, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'))
            """,
            (
                pipeline_run_id,
                pipeline_type,
                dataset_id,
                status,
                records_processed,
                error_message,
                json.dumps(step_details or {})
            )
        )


def update_pipeline_run(
    pipeline_run_id: str,
    status: str,
    records_processed: Optional[int] = None,
    error_message: Optional[str] = None,
    step_details: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Update pipeline execution status and completion timestamp."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE pipeline_runs
            SET status = ?,
                records_processed = COALESCE(?, records_processed),
                error_message = ?,
                step_details_json = COALESCE(?, step_details_json),
                completed_at = datetime('now', 'utc')
            WHERE pipeline_run_id = ?
            """,
            (
                status,
                records_processed,
                error_message,
                json.dumps(step_details) if step_details is not None else None,
                pipeline_run_id
            )
        )


def insert_solar_observations_bulk(
    observations: List[Dict[str, Any]],
    db_path: Optional[Union[str, Path]] = None
) -> int:
    """Bulk insert structured solar observations."""
    if not observations:
        return 0

    with get_connection(db_path) as conn:
        data = [
            (
                obs["observation_id"],
                obs["dataset_id"],
                obs["asset_id"],
                obs["location_id"],
                obs["latitude"],
                obs["longitude"],
                obs.get("observation_time"),
                obs.get("ghi_kwh_m2_day"),
                obs.get("dni_kwh_m2_day"),
                obs.get("dhi_kwh_m2_day"),
                obs.get("sunshine_duration_hours"),
                obs.get("rooftop_area_m2"),
                obs.get("suitability_score"),
                json.dumps(obs.get("metadata") or {})
            )
            for obs in observations
        ]
        conn.executemany(
            """
            INSERT INTO solar_observations (
                observation_id, dataset_id, asset_id, location_id, latitude, longitude,
                observation_time, ghi_kwh_m2_day, dni_kwh_m2_day, dhi_kwh_m2_day,
                sunshine_duration_hours, rooftop_area_m2, suitability_score, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            data
        )
        return len(data)


def insert_vector_index_record(
    vector_record_id: str,
    dataset_id: str,
    asset_id: str,
    collection_name: str,
    chunk_index: int,
    content_hash: str,
    chunk_text: str,
    embedding_model: str,
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Record vector document chunk index mapping in SQLite."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO vector_index_records (
                vector_record_id, dataset_id, asset_id, collection_name, chunk_index,
                content_hash, chunk_text, embedding_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vector_record_id,
                dataset_id,
                asset_id,
                collection_name,
                chunk_index,
                content_hash,
                chunk_text,
                embedding_model
            )
        )


def insert_analysis_run(
    analysis_id: str,
    location_id: str,
    algorithm_name: str,
    algorithm_version: str,
    configuration: Dict[str, Any],
    assumptions: Dict[str, Any],
    status: str = "running",
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Record start of a solar potential analysis run."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO analysis_runs (
                analysis_id, location_id, algorithm_name, algorithm_version,
                status, configuration_json, assumptions_json, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'))
            """,
            (
                analysis_id,
                location_id,
                algorithm_name,
                algorithm_version,
                status,
                json.dumps(configuration),
                json.dumps(assumptions)
            )
        )


def link_analysis_input(
    analysis_id: str,
    dataset_id: str,
    asset_id: Optional[str] = None,
    role: str = "primary_irradiance",
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Record input lineage for an analysis run."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO analysis_inputs (analysis_id, dataset_id, asset_id, role)
            VALUES (?, ?, ?, ?)
            """,
            (analysis_id, dataset_id, asset_id, role)
        )


# Alias for compatibility
insert_analysis_input = link_analysis_input


def insert_analysis_result(
    result_id: str,
    analysis_id: str,
    metric_name: str,
    numerical_value: float,
    unit: str,
    confidence_score: Optional[float] = None,
    output_uri: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Record a computed analysis metric result."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO analysis_results (
                result_id, analysis_id, metric_name, numerical_value, unit,
                confidence_score, output_uri, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                analysis_id,
                metric_name,
                numerical_value,
                unit,
                confidence_score,
                output_uri,
                json.dumps(metadata or {})
            )
        )


def update_analysis_run(
    analysis_id: str,
    status: str,
    error_message: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Mark an analysis run as completed or failed."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE analysis_runs
            SET status = ?, error_message = ?, completed_at = datetime('now', 'utc')
            WHERE analysis_id = ?
            """,
            (status, error_message, analysis_id)
        )


def get_analysis_lineage(
    analysis_id: str, db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """Retrieve full analysis report with its inputs, configuration, and results."""
    run = execute_query("SELECT * FROM analysis_runs WHERE analysis_id = ?", (analysis_id,), db_path)
    if not run:
        return {}

    inputs = execute_query(
        """
        SELECT ai.*, d.name AS dataset_name, d.data_type, d.version, a.original_filename
        FROM analysis_inputs ai
        JOIN datasets d ON ai.dataset_id = d.dataset_id
        LEFT JOIN assets a ON ai.asset_id = a.asset_id
        WHERE ai.analysis_id = ?
        """,
        (analysis_id,),
        db_path
    )
    results = execute_query(
        "SELECT * FROM analysis_results WHERE analysis_id = ?", (analysis_id,), db_path
    )
    return {
        "run": run[0],
        "inputs": inputs,
        "results": results
    }
