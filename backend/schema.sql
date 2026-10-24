-- schema.sql - Authoritative Relational and Metadata Catalogue Schema
-- Solar Atlas: Space Hackathon AI for Solar Energy Potential Mapping
-- Enforces SQLite Foreign Keys, Check Constraints, Timestamps, and Validated Schema.

PRAGMA foreign_keys = ON;

-- 1. Data Sources (e.g., ISRO Bhuvan, SAC VEDAS, Synthetic Demonstration)
CREATE TABLE IF NOT EXISTS data_sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    provider_type TEXT NOT NULL,
    base_url TEXT,
    auth_type TEXT NOT NULL DEFAULT 'none',
    contact_email TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 2. Geographic Locations / Study Areas
CREATE TABLE IF NOT EXISTS locations (
    location_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state TEXT,
    country TEXT NOT NULL DEFAULT 'India',
    center_lat REAL NOT NULL CHECK(center_lat >= -90.0 AND center_lat <= 90.0),
    center_lon REAL NOT NULL CHECK(center_lon >= -180.0 AND center_lon <= 180.0),
    bounding_box_json TEXT,
    crs TEXT NOT NULL DEFAULT 'EPSG:4326',
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    CONSTRAINT uq_location_name_region UNIQUE (name, state, country)
);

-- 3. Datasets (Logical dataset versions and lifecycle states)
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    location_id TEXT NOT NULL REFERENCES locations(location_id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    format TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT 'v1.0',
    idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('registered', 'stored', 'indexing', 'ready', 'quarantined', 'failed')),
    acquisition_date_start TEXT,
    acquisition_date_end TEXT,
    spatial_resolution_meters REAL,
    quality_score REAL DEFAULT 1.0 CHECK(quality_score >= 0.0 AND quality_score <= 1.0),
    is_demonstration INTEGER NOT NULL DEFAULT 1 CHECK(is_demonstration IN (0, 1)),
    quarantine_reason TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 4. Dataset Geographic Areas
CREATE TABLE IF NOT EXISTS dataset_areas (
    area_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    location_id TEXT NOT NULL REFERENCES locations(location_id) ON DELETE CASCADE,
    min_lat REAL NOT NULL CHECK(min_lat >= -90.0 AND min_lat <= 90.0),
    max_lat REAL NOT NULL CHECK(max_lat >= -90.0 AND max_lat <= 90.0),
    min_lon REAL NOT NULL CHECK(min_lon >= -180.0 AND min_lon <= 180.0),
    max_lon REAL NOT NULL CHECK(max_lon >= -180.0 AND max_lon <= 180.0),
    srid INTEGER NOT NULL DEFAULT 4326,
    CONSTRAINT uq_dataset_location_area UNIQUE (dataset_id, location_id)
);

-- 5. Physical Assets (Raw files, rasters, vectors, tables stored on disk)
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    storage_uri TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL CHECK(file_size_bytes >= 0),
    mime_type TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    crs TEXT,
    bounds_json TEXT,
    resolution_meters REAL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 6. Pipeline Executions (Tracks ingestion, indexing, and processing audits)
CREATE TABLE IF NOT EXISTS pipeline_runs (
    pipeline_run_id TEXT PRIMARY KEY,
    pipeline_type TEXT NOT NULL,
    dataset_id TEXT REFERENCES datasets(dataset_id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed', 'quarantined')),
    records_processed INTEGER DEFAULT 0,
    error_message TEXT,
    step_details_json TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    completed_at TEXT
);

-- 7. Dataset Lineage (Traceability of derived or processed datasets)
CREATE TABLE IF NOT EXISTS dataset_lineage (
    lineage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE RESTRICT,
    child_dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    transformation_type TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    CONSTRAINT uq_dataset_lineage UNIQUE (parent_dataset_id, child_dataset_id, transformation_type)
);

-- 8. Solar Structured Observations (Point/tabular measurements for GIS and physics calculations)
CREATE TABLE IF NOT EXISTS solar_observations (
    observation_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    location_id TEXT NOT NULL REFERENCES locations(location_id) ON DELETE RESTRICT,
    latitude REAL NOT NULL CHECK(latitude >= -90.0 AND latitude <= 90.0),
    longitude REAL NOT NULL CHECK(longitude >= -180.0 AND longitude <= 180.0),
    observation_time TEXT,
    ghi_kwh_m2_day REAL CHECK(ghi_kwh_m2_day IS NULL OR ghi_kwh_m2_day >= 0.0),
    dni_kwh_m2_day REAL CHECK(dni_kwh_m2_day IS NULL OR dni_kwh_m2_day >= 0.0),
    dhi_kwh_m2_day REAL CHECK(dhi_kwh_m2_day IS NULL OR dhi_kwh_m2_day >= 0.0),
    sunshine_duration_hours REAL CHECK(sunshine_duration_hours IS NULL OR (sunshine_duration_hours >= 0.0 AND sunshine_duration_hours <= 24.0)),
    rooftop_area_m2 REAL CHECK(rooftop_area_m2 IS NULL OR rooftop_area_m2 >= 0.0),
    suitability_score REAL CHECK(suitability_score IS NULL OR (suitability_score >= 0.0 AND suitability_score <= 100.0)),
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 9. Vector Index Records (Bi-directional link between ChromaDB chunks and SQLite records)
CREATE TABLE IF NOT EXISTS vector_index_records (
    vector_record_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    collection_name TEXT NOT NULL,
    chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
    content_hash TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    CONSTRAINT uq_vector_chunk UNIQUE (dataset_id, asset_id, chunk_index)
);

-- 10. Solar Analysis Runs
CREATE TABLE IF NOT EXISTS analysis_runs (
    analysis_id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(location_id) ON DELETE RESTRICT,
    algorithm_name TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed')),
    configuration_json TEXT NOT NULL,
    assumptions_json TEXT NOT NULL,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    completed_at TEXT
);

-- 11. Analysis Inputs (Lineage tracking input datasets and assets for each analysis)
CREATE TABLE IF NOT EXISTS analysis_inputs (
    analysis_input_id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL REFERENCES analysis_runs(analysis_id) ON DELETE CASCADE,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE RESTRICT,
    asset_id TEXT REFERENCES assets(asset_id) ON DELETE RESTRICT,
    role TEXT NOT NULL DEFAULT 'primary_irradiance',
    CONSTRAINT uq_analysis_input UNIQUE (analysis_id, dataset_id, role)
);

-- 12. Analysis Results (Structured outputs, metrics, and report links)
CREATE TABLE IF NOT EXISTS analysis_results (
    result_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analysis_runs(analysis_id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    numerical_value REAL NOT NULL,
    unit TEXT NOT NULL,
    confidence_score REAL CHECK(confidence_score IS NULL OR (confidence_score >= 0.0 AND confidence_score <= 1.0)),
    output_uri TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- Indices for performance and filtering
CREATE INDEX IF NOT EXISTS idx_datasets_source ON datasets(source_id);
CREATE INDEX IF NOT EXISTS idx_datasets_location ON datasets(location_id);
CREATE INDEX IF NOT EXISTS idx_datasets_status ON datasets(status);
CREATE INDEX IF NOT EXISTS idx_datasets_type ON datasets(data_type);
CREATE INDEX IF NOT EXISTS idx_datasets_dates ON datasets(acquisition_date_start, acquisition_date_end);
CREATE INDEX IF NOT EXISTS idx_assets_dataset ON assets(dataset_id);
CREATE INDEX IF NOT EXISTS idx_assets_checksum ON assets(checksum_sha256);
CREATE INDEX IF NOT EXISTS idx_observations_dataset ON solar_observations(dataset_id);
CREATE INDEX IF NOT EXISTS idx_observations_location ON solar_observations(location_id);
CREATE INDEX IF NOT EXISTS idx_observations_coords ON solar_observations(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_vector_records_dataset ON vector_index_records(dataset_id);
CREATE INDEX IF NOT EXISTS idx_vector_records_asset ON vector_index_records(asset_id);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_location ON analysis_runs(location_id);
CREATE INDEX IF NOT EXISTS idx_analysis_results_analysis ON analysis_results(analysis_id);
