"""
backend - Solar Atlas Core Backend Package
Space Hackathon: AI for Solar Energy Potential Mapping using Remote Sensing & GIS

Exposes:
- database: Authoritative SQLite catalogue manager and query layer
- id_utils: Collision-resistant UUID-based IDs and SHA-256 cryptographic checksums
- gis_processor: GIS metadata extractors, synthetic generators, and Bhuvan/VEDAS interfaces
- ingestion_pipeline: Pipeline A (ingestion, validation, checksums, SQLite registration, ChromaDB)
- retrieval_pipeline: Pipeline B (multi-criteria SQL filter, ChromaDB semantic search, 6-point check)
- solar_backend: Physics-grounded solar yield, capacity, and CEA CO2 analysis engine
"""

from . import database
from . import id_utils
from . import gis_processor
from . import ingestion_pipeline
from . import retrieval_pipeline
from . import solar_backend

from .database import (
    initialize_database,
    get_connection,
    get_db_path,
    execute_query,
    execute_scalar,
    get_dataset,
    get_assets_by_dataset,
    get_analysis_lineage,
)
from .id_utils import (
    calculate_sha256,
    generate_source_id,
    generate_location_id,
    generate_dataset_id,
    generate_asset_id,
    generate_observation_id,
    generate_vector_record_id,
    generate_pipeline_run_id,
    generate_analysis_id,
    generate_result_id,
)
from .ingestion_pipeline import (
    ingest_dataset,
    rebuild_or_retry_indexing,
    get_chroma_collection,
)
from .retrieval_pipeline import (
    RetrievalRequest,
    retrieve_datasets,
)
from .solar_backend import (
    SolarAnalysisAssumptions,
    calculate_solar_potential,
    run_solar_analysis,
    INDIA_GRID_EMISSION_FACTOR_KG_PER_KWH,
)

__all__ = [
    "database",
    "id_utils",
    "gis_processor",
    "ingestion_pipeline",
    "retrieval_pipeline",
    "solar_backend",
    "initialize_database",
    "get_connection",
    "get_db_path",
    "execute_query",
    "execute_scalar",
    "get_dataset",
    "get_assets_by_dataset",
    "get_analysis_lineage",
    "calculate_sha256",
    "generate_source_id",
    "generate_location_id",
    "generate_dataset_id",
    "generate_asset_id",
    "generate_observation_id",
    "generate_vector_record_id",
    "generate_pipeline_run_id",
    "generate_analysis_id",
    "generate_result_id",
    "ingest_dataset",
    "rebuild_or_retry_indexing",
    "get_chroma_collection",
    "RetrievalRequest",
    "retrieve_datasets",
    "SolarAnalysisAssumptions",
    "calculate_solar_potential",
    "run_solar_analysis",
    "INDIA_GRID_EMISSION_FACTOR_KG_PER_KWH",
]
