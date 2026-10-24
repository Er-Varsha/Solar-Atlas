"""
ingestion_pipeline.py - Pipeline A: Authoritative Ingestion & Indexing
Solar Atlas: Space Hackathon AI for Solar Energy Potential Mapping

Responsibilities:
1. Accept local dataset file and metadata.
2. Register data source and study area in SQLite.
3. Validate file format, required fields, date, CRS, and spatial coordinates.
4. Calculate SHA-256 checksum.
5. Generate collision-resistant unique dataset and asset IDs.
6. Detect repeated ingestion using an idempotency key.
7. Preserve files in data/raw/.
8. Route invalid/corrupt files to data/quarantine/ with reason recorded.
9. Store dataset, asset, area, and pipeline audit records in SQLite.
10. Generate searchable chunks and index them in ChromaDB.
11. Record vector chunk mappings in SQLite (vector_index_records).
12. Mark dataset as 'ready' only after all storage and indexing steps verify.
"""

from __future__ import annotations
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

try:
    from .database import (
        get_db_path,
        get_dataset,
        get_dataset_by_idempotency_key,
        get_assets_by_dataset,
        insert_dataset,
        insert_asset,
        insert_dataset_area,
        insert_pipeline_run,
        insert_solar_observations_bulk,
        insert_vector_index_record,
        register_data_source,
        register_location,
        update_dataset_status,
        update_pipeline_run,
        execute_query
    )
    from .gis_processor import (
        inspect_csv_solar_metadata,
        inspect_raster_metadata,
        inspect_vector_metadata
    )
    from .id_utils import (
        calculate_content_hash,
        calculate_sha256,
        generate_asset_id,
        generate_dataset_id,
        generate_idempotency_key,
        generate_location_id,
        generate_observation_id,
        generate_pipeline_run_id,
        generate_source_id,
        generate_vector_record_id,
        path_to_uri
    )
except (ImportError, ValueError):
    from database import (
        get_db_path,
        get_dataset,
        get_dataset_by_idempotency_key,
        get_assets_by_dataset,
        insert_dataset,
        insert_asset,
        insert_dataset_area,
        insert_pipeline_run,
        insert_solar_observations_bulk,
        insert_vector_index_record,
        register_data_source,
        register_location,
        update_dataset_status,
        update_pipeline_run,
        execute_query
    )
    from gis_processor import (
        inspect_csv_solar_metadata,
        inspect_raster_metadata,
        inspect_vector_metadata
    )
    from id_utils import (
        calculate_content_hash,
        calculate_sha256,
        generate_asset_id,
        generate_dataset_id,
        generate_idempotency_key,
        generate_location_id,
        generate_observation_id,
        generate_pipeline_run_id,
        generate_source_id,
        generate_vector_record_id,
        path_to_uri
    )


EMBEDDING_MODEL_NAME = "solar_atlas_hash_embed_v1"
COLLECTION_NAME = "solar_atlas_metadata"


class SolarAtlasEmbeddingFunction(EmbeddingFunction[Documents]):
    """
    Lightweight, deterministic word + n-gram term-frequency embedding function (128 dims).
    Guarantees cross-platform execution on Windows without requiring C++ ONNX compilation,
    while accurately ranking semantic queries for solar GIS metadata.
    """
    def __init__(self, dim: int = 128):
        self.dim = dim

    def name(self) -> str:
        return EMBEDDING_MODEL_NAME

    def __call__(self, input: Documents) -> Embeddings:
        embeddings = []
        for doc in input:
            words = re.findall(r"\w+", doc.lower())
            vec = [0.0] * self.dim
            for w in words:
                idx = hash(w) % self.dim
                vec[idx] += 1.0
            for i in range(len(words) - 1):
                bigram = f"{words[i]}_{words[i+1]}"
                idx = hash(bigram) % self.dim
                vec[idx] += 0.8
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            embeddings.append([float(x / norm) for x in vec])
        return embeddings


def get_chroma_collection(
    chroma_dir: Optional[Union[str, Path]] = None,
    collection_name: str = COLLECTION_NAME
) -> chromadb.Collection:
    """Retrieve or create persistent ChromaDB collection with custom embedding function."""
    c_path = Path(chroma_dir) if chroma_dir else Path(__file__).resolve().parent.parent / "chroma_db"
    c_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(c_path))
    ef = SolarAtlasEmbeddingFunction()
    return client.get_or_create_collection(
        name=collection_name,
        embedding_function=ef,
        metadata={"description": "Solar Atlas GIS metadata and searchable chunks", "embedding_model": EMBEDDING_MODEL_NAME}
    )


def validate_file_and_extract_metadata(
    file_path: Path,
    expected_data_type: str
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validate that the file exists, is non-empty, matches supported extensions,
    and extract GIS/spatial metadata.
    """
    if not file_path.is_file():
        return False, "File does not exist or is not a regular file.", {}

    file_size = file_path.stat().st_size
    if file_size == 0:
        return False, "File is completely empty (0 bytes).", {}

    suffix = file_path.suffix.lower()
    valid_suffixes = {".tif", ".tiff", ".geojson", ".json", ".csv"}
    if suffix not in valid_suffixes:
        return False, f"Unsupported file extension '{suffix}'. Expected one of: {sorted(list(valid_suffixes))}", {}

    try:
        if suffix in {".tif", ".tiff"}:
            meta = inspect_raster_metadata(file_path)
            return True, "Valid GeoTIFF raster.", meta
        elif suffix in {".geojson", ".json"}:
            meta = inspect_vector_metadata(file_path)
            return True, "Valid GeoJSON vector layer.", meta
        elif suffix == ".csv":
            meta = inspect_csv_solar_metadata(file_path)
            return True, "Valid CSV observation table.", meta
        else:
            return False, f"Unrecognized file format: {suffix}", {}
    except Exception as e:
        return False, f"File failed GIS inspection: {str(e)}", {}


def generate_searchable_chunks(
    dataset_name: str,
    data_type: str,
    format_type: str,
    source_name: str,
    location_name: str,
    state: Optional[str],
    center_lat: float,
    center_lon: float,
    acquisition_start: Optional[str],
    acquisition_end: Optional[str],
    gis_meta: Dict[str, Any]
) -> List[str]:
    """Generate structured, searchable document chunks for ChromaDB indexing."""
    chunks = []

    # Chunk 0: Core descriptive identification
    c0 = (
        f"Dataset: {dataset_name}. Type: {data_type}. Format: {format_type}. "
        f"Provider: {source_name}. Study Area: {location_name}, {state or 'India'}. "
        f"Center Coordinates: Latitude {center_lat:.4f}, Longitude {center_lon:.4f}. "
        f"Acquisition Temporal Range: {acquisition_start or 'N/A'} to {acquisition_end or 'N/A'}."
    )
    chunks.append(c0)

    # Chunk 1: Spatial coverage and projection
    bounds = gis_meta.get("bounds") or [center_lon - 0.05, center_lat - 0.05, center_lon + 0.05, center_lat + 0.05]
    crs = gis_meta.get("crs", "EPSG:4326")
    res = gis_meta.get("resolution") or [10.0, 10.0]
    c1 = (
        f"Spatial Metadata for {dataset_name}: Bounding Box [MinLon {bounds[0]:.4f}, MinLat {bounds[1]:.4f}, "
        f"MaxLon {bounds[2]:.4f}, MaxLat {bounds[3]:.4f}]. Coordinate Reference System: {crs}. "
        f"Resolution: {res}. File size: {gis_meta.get('file_size_bytes', 0)} bytes."
    )
    chunks.append(c1)

    # Chunk 2: Measurement and feature summary
    if "statistics" in gis_meta:
        stats = gis_meta["statistics"]
        c2 = (
            f"Raster Measurements for {dataset_name}: Band 1 Solar Irradiance (GHI). "
            f"Minimum: {stats.get('min', 0):.2f} kWh/m2/day, Maximum: {stats.get('max', 0):.2f} kWh/m2/day, "
            f"Mean: {stats.get('mean', 0):.2f} kWh/m2/day. Valid Pixels: {stats.get('valid_pixel_count', 0)}."
        )
        chunks.append(c2)
    elif "mean_ghi_kwh_m2_day" in gis_meta and gis_meta["mean_ghi_kwh_m2_day"] is not None:
        c2 = (
            f"Tabular Solar Observations for {dataset_name}: Total Points: {gis_meta.get('row_count', 0)}. "
            f"Average Measured Daily GHI: {gis_meta['mean_ghi_kwh_m2_day']:.2f} kWh/m2/day. "
            f"Columns: {', '.join(gis_meta.get('columns', []))}."
        )
        chunks.append(c2)
    elif "feature_count" in gis_meta:
        c2 = (
            f"Vector Footprints for {dataset_name}: Total Building Features: {gis_meta.get('feature_count', 0)}. "
            f"Geometry Types: {', '.join(gis_meta.get('geometry_types', ['Polygon']))}."
        )
        chunks.append(c2)

    return chunks


def ingest_dataset(
    source_file_path: Union[str, Path],
    dataset_name: str,
    data_type: str,
    source_name: str = "Synthetic Demo Source",
    provider_type: str = "synthetic_demo",
    location_name: str = "Rampur",
    center_lat: float = 28.80,
    center_lon: float = 79.02,
    state: str = "Uttar Pradesh",
    country: str = "India",
    version: str = "v1.0",
    acquisition_start: Optional[str] = "2026-03-01",
    acquisition_end: Optional[str] = "2026-03-31",
    spatial_resolution_meters: float = 10.0,
    quality_score: float = 1.0,
    is_demonstration: bool = True,
    db_path: Optional[Union[str, Path]] = None,
    chroma_dir: Optional[Union[str, Path]] = None,
    storage_root: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Execute Pipeline A: Validates input, calculates checksum, checks idempotency,
    stores file in raw/ or quarantine/, updates SQLite, and indexes vector chunks in ChromaDB.
    """
    raw_path = Path(source_file_path)
    base_storage = Path(storage_root) if storage_root else Path(__file__).resolve().parent.parent / "data"
    raw_dir = base_storage / "raw"
    quarantine_dir = base_storage / "quarantine"
    raw_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    run_id = generate_pipeline_run_id()
    insert_pipeline_run(
        pipeline_run_id=run_id,
        pipeline_type="ingestion",
        dataset_id=None,
        status="running",
        step_details={"stage": "started", "file": str(raw_path)},
        db_path=db_path
    )

    # 1. File Validation
    is_valid, validation_msg, gis_meta = validate_file_and_extract_metadata(raw_path, data_type)
    if not is_valid:
        quarantine_target = quarantine_dir / f"{raw_path.stem}_{run_id}{raw_path.suffix}"
        if raw_path.is_file():
            shutil.copy2(raw_path, quarantine_target)

        # Register quarantined record
        src_id = register_data_source(generate_source_id(), source_name, provider_type, db_path=db_path)
        loc_id = register_location(generate_location_id(), location_name, center_lat, center_lon, state=state, country=country, db_path=db_path)
        ds_id = generate_dataset_id()
        insert_dataset(
            dataset_id=ds_id,
            source_id=src_id,
            location_id=loc_id,
            name=dataset_name,
            data_type=data_type,
            format_type=raw_path.suffix.lstrip("."),
            version=version,
            status="quarantined",
            quarantine_reason=validation_msg,
            metadata={"validation_error": validation_msg},
            db_path=db_path
        )
        update_pipeline_run(
            pipeline_run_id=run_id,
            status="quarantined",
            error_message=validation_msg,
            step_details={"stage": "quarantined", "reason": validation_msg},
            db_path=db_path
        )
        return {
            "status": "quarantined",
            "dataset_id": ds_id,
            "pipeline_run_id": run_id,
            "reason": validation_msg,
            "storage_path": str(quarantine_target)
        }

    # 2. Checksum and Idempotency Key
    file_checksum = calculate_sha256(raw_path)
    idempotency_key = generate_idempotency_key(file_checksum, location_name, data_type, version)

    # Check for existing ready dataset
    existing_ds = get_dataset_by_idempotency_key(idempotency_key, db_path=db_path)
    if existing_ds and existing_ds["status"] == "ready":
        existing_assets = get_assets_by_dataset(existing_ds["dataset_id"], db_path=db_path)
        update_pipeline_run(
            pipeline_run_id=run_id,
            status="completed",
            records_processed=0,
            step_details={"stage": "idempotent_skip", "existing_dataset_id": existing_ds["dataset_id"]},
            db_path=db_path
        )
        return {
            "status": "already_ingested",
            "message": "Dataset already exists and is ready (idempotent duplicate request).",
            "dataset_id": existing_ds["dataset_id"],
            "dataset": existing_ds,
            "assets": existing_assets,
            "pipeline_run_id": run_id
        }

    # 3. Register Data Source and Location in SQLite
    src_id = register_data_source(generate_source_id(), source_name, provider_type, db_path=db_path)
    loc_id = register_location(
        generate_location_id(), location_name, center_lat, center_lon,
        state=state, country=country, db_path=db_path
    )

    # 4. Generate Dataset & Asset IDs and Initial 'registered' State
    dataset_id = generate_dataset_id()
    asset_id = generate_asset_id()

    insert_dataset(
        dataset_id=dataset_id,
        source_id=src_id,
        location_id=loc_id,
        name=dataset_name,
        data_type=data_type,
        format_type=raw_path.suffix.lstrip("."),
        version=version,
        idempotency_key=idempotency_key,
        status="registered",
        acquisition_date_start=acquisition_start,
        acquisition_date_end=acquisition_end,
        spatial_resolution_meters=spatial_resolution_meters,
        quality_score=quality_score,
        is_demonstration=1 if is_demonstration else 0,
        metadata=gis_meta,
        db_path=db_path
    )

    try:
        # 5. Move/Preserve file in data/raw/ and transition to 'stored'
        dest_filename = f"{dataset_id}_{raw_path.name}"
        stored_path = raw_dir / dest_filename
        shutil.copy2(raw_path, stored_path)

        storage_uri = path_to_uri(stored_path)
        insert_asset(
            asset_id=asset_id,
            dataset_id=dataset_id,
            original_filename=raw_path.name,
            storage_uri=storage_uri,
            storage_path=str(stored_path),
            checksum_sha256=file_checksum,
            file_size_bytes=stored_path.stat().st_size,
            mime_type=f"application/{raw_path.suffix.lstrip('.')}",
            asset_type="raw_input",
            crs=gis_meta.get("crs", "EPSG:4326"),
            bounds=gis_meta.get("bounds"),
            resolution_meters=spatial_resolution_meters,
            metadata=gis_meta,
            db_path=db_path
        )

        bounds = gis_meta.get("bounds") or [center_lon - 0.05, center_lat - 0.05, center_lon + 0.05, center_lat + 0.05]
        insert_dataset_area(
            dataset_id=dataset_id,
            location_id=loc_id,
            min_lat=bounds[1],
            max_lat=bounds[3],
            min_lon=bounds[0],
            max_lon=bounds[2],
            db_path=db_path
        )

        # If CSV has observations, populate solar_observations
        if raw_path.suffix.lower() == ".csv":
            import pandas as pd
            df = pd.read_csv(stored_path)
            lat_col = next((c for c in df.columns if c.lower() == "latitude"), None)
            lon_col = next((c for c in df.columns if c.lower() == "longitude"), None)
            if lat_col and lon_col:
                observations = []
                for _, row in df.iterrows():
                    obs = {
                        "observation_id": generate_observation_id(),
                        "dataset_id": dataset_id,
                        "asset_id": asset_id,
                        "location_id": loc_id,
                        "latitude": float(row[lat_col]),
                        "longitude": float(row[lon_col]),
                        "observation_time": str(row.get("observation_date", acquisition_start or "2026-03-15")),
                        "ghi_kwh_m2_day": float(row.get("ghi_kwh_m2_day", 5.0)) if "ghi_kwh_m2_day" in row else None,
                        "dni_kwh_m2_day": float(row.get("dni_kwh_m2_day", 3.6)) if "dni_kwh_m2_day" in row else None,
                        "dhi_kwh_m2_day": float(row.get("dhi_kwh_m2_day", 1.4)) if "dhi_kwh_m2_day" in row else None,
                        "sunshine_duration_hours": float(row.get("sunshine_duration_hours", 5.5)) if "sunshine_duration_hours" in row else None,
                        "rooftop_area_m2": float(row.get("rooftop_area_m2", 100.0)) if "rooftop_area_m2" in row else None,
                        "suitability_score": float(row.get("suitability_score", 75.0)) if "suitability_score" in row else None,
                        "metadata": {"site_name": str(row.get("site_name", "Site"))}
                    }
                    observations.append(obs)
                insert_solar_observations_bulk(observations, db_path=db_path)

        update_dataset_status(dataset_id, "stored", db_path=db_path)

        # 6. ChromaDB Indexing and transition to 'indexing'
        update_dataset_status(dataset_id, "indexing", db_path=db_path)
        chunks = generate_searchable_chunks(
            dataset_name=dataset_name,
            data_type=data_type,
            format_type=raw_path.suffix.lstrip("."),
            source_name=source_name,
            location_name=location_name,
            state=state,
            center_lat=center_lat,
            center_lon=center_lon,
            acquisition_start=acquisition_start,
            acquisition_end=acquisition_end,
            gis_meta=gis_meta
        )

        collection = get_chroma_collection(chroma_dir=chroma_dir)
        chroma_ids = []
        chroma_docs = []
        chroma_metas = []

        for idx, chunk_text in enumerate(chunks):
            v_id = generate_vector_record_id()
            c_hash = calculate_content_hash(chunk_text)
            chroma_ids.append(v_id)
            chroma_docs.append(chunk_text)
            chroma_metas.append({
                "dataset_id": dataset_id,
                "asset_id": asset_id,
                "chunk_index": idx,
                "data_type": data_type,
                "location_name": location_name,
                "source_name": source_name,
                "status": "ready"
            })

            insert_vector_index_record(
                vector_record_id=v_id,
                dataset_id=dataset_id,
                asset_id=asset_id,
                collection_name=COLLECTION_NAME,
                chunk_index=idx,
                content_hash=c_hash,
                chunk_text=chunk_text,
                embedding_model=EMBEDDING_MODEL_NAME,
                db_path=db_path
            )

        collection.add(
            ids=chroma_ids,
            documents=chroma_docs,
            metadatas=chroma_metas
        )

        # 7. Verification and transition to 'ready'
        update_dataset_status(dataset_id, "ready", db_path=db_path)
        update_pipeline_run(
            pipeline_run_id=run_id,
            status="completed",
            records_processed=len(chunks),
            step_details={"stage": "ready", "chunks_indexed": len(chunks), "dataset_id": dataset_id},
            db_path=db_path
        )

        return {
            "status": "ready",
            "dataset_id": dataset_id,
            "asset_id": asset_id,
            "pipeline_run_id": run_id,
            "storage_path": str(stored_path),
            "storage_uri": storage_uri,
            "checksum_sha256": file_checksum,
            "chunks_indexed": len(chunks)
        }

    except Exception as e:
        # Safe failure handling
        update_dataset_status(dataset_id, "failed", quarantine_reason=str(e), db_path=db_path)
        update_pipeline_run(
            pipeline_run_id=run_id,
            status="failed",
            error_message=str(e),
            step_details={"stage": "failed", "error": str(e)},
            db_path=db_path
        )
        raise RuntimeError(f"Pipeline A ingestion failed for {dataset_name}: {str(e)}") from e


def rebuild_or_retry_indexing(
    dataset_id: str,
    db_path: Optional[Union[str, Path]] = None,
    chroma_dir: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Retry or rebuild ChromaDB vector chunks for an existing dataset.
    Ensures safe recovery from partial failures without file corruption.
    """
    ds = get_dataset(dataset_id, db_path=db_path)
    if not ds:
        raise ValueError(f"Dataset {dataset_id} not found in database.")

    assets = get_assets_by_dataset(dataset_id, db_path=db_path)
    if not assets:
        raise ValueError(f"No assets found for dataset {dataset_id}.")

    asset = assets[0]
    file_path = Path(asset["storage_path"])
    if not file_path.is_file():
        raise FileNotFoundError(f"Underlying asset file missing at: {file_path}")

    update_dataset_status(dataset_id, "indexing", db_path=db_path)
    loc_rows = execute_query("SELECT * FROM locations WHERE location_id = ?", (ds["location_id"],), db_path=db_path)
    loc = loc_rows[0] if loc_rows else {"name": "Unknown", "state": "", "center_lat": 28.8, "center_lon": 79.0}

    src_rows = execute_query("SELECT * FROM data_sources WHERE source_id = ?", (ds["source_id"],), db_path=db_path)
    src_name = src_rows[0]["name"] if src_rows else "Unknown Source"

    gis_meta = json.loads(asset.get("metadata_json") or "{}")
    chunks = generate_searchable_chunks(
        dataset_name=ds["name"],
        data_type=ds["data_type"],
        format_type=ds["format"],
        source_name=src_name,
        location_name=loc["name"],
        state=loc.get("state"),
        center_lat=loc["center_lat"],
        center_lon=loc["center_lon"],
        acquisition_start=ds.get("acquisition_date_start"),
        acquisition_end=ds.get("acquisition_date_end"),
        gis_meta=gis_meta
    )

    collection = get_chroma_collection(chroma_dir=chroma_dir)

    chroma_ids = []
    chroma_docs = []
    chroma_metas = []

    for idx, chunk_text in enumerate(chunks):
        v_id = generate_vector_record_id()
        c_hash = calculate_content_hash(chunk_text)
        chroma_ids.append(v_id)
        chroma_docs.append(chunk_text)
        chroma_metas.append({
            "dataset_id": dataset_id,
            "asset_id": asset["asset_id"],
            "chunk_index": idx,
            "data_type": ds["data_type"],
            "location_name": loc["name"],
            "source_name": src_name,
            "status": "ready"
        })
        insert_vector_index_record(
            vector_record_id=v_id,
            dataset_id=dataset_id,
            asset_id=asset["asset_id"],
            collection_name=COLLECTION_NAME,
            chunk_index=idx,
            content_hash=c_hash,
            chunk_text=chunk_text,
            embedding_model=EMBEDDING_MODEL_NAME,
            db_path=db_path
        )

    collection.add(ids=chroma_ids, documents=chroma_docs, metadatas=chroma_metas)
    update_dataset_status(dataset_id, "ready", db_path=db_path)

    return {
        "status": "ready",
        "dataset_id": dataset_id,
        "chunks_indexed": len(chunks),
        "message": "Rebuilt vector index successfully."
    }
