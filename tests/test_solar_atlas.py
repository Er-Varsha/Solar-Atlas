"""
tests/test_solar_atlas.py - Comprehensive Test Suite
Solar Atlas: Space Hackathon AI for Solar Energy Potential Mapping

Tests all 14 mandatory requirements:
1. Database creation and initialization.
2. Unique ID generation.
3. Foreign-key validation.
4. Dataset registration.
5. Duplicate ingestion and idempotent retries.
6. File checksum verification.
7. ChromaDB indexing and retrieval.
8. Retrieval with different location and date filters.
9. Prevention of cross-dataset record mixing.
10. Invalid or missing file handling (quarantine).
11. Failed indexing and retry behavior.
12. Solar calculation correctness.
13. Analysis results linked to correct input datasets.
14. No-result retrieval behavior.

All tests run on isolated temporary databases and directories.
"""

from __future__ import annotations
import os
from pathlib import Path
import sqlite3
import sys

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import backend.database as database
import backend.gis_processor as gis_processor
from backend.id_utils import (
    calculate_sha256,
    generate_analysis_id,
    generate_asset_id,
    generate_dataset_id,
    generate_location_id,
    generate_observation_id,
    generate_pipeline_run_id,
    generate_result_id,
    generate_source_id,
    generate_vector_record_id
)
from backend.ingestion_pipeline import (
    ingest_dataset,
    rebuild_or_retry_indexing,
    get_chroma_collection
)
from backend.retrieval_pipeline import RetrievalRequest, retrieve_datasets
from backend.solar_backend import (
    SolarAnalysisAssumptions,
    calculate_solar_potential,
    run_solar_analysis,
    INDIA_GRID_EMISSION_FACTOR_KG_PER_KWH
)


@pytest.fixture
def temp_env(tmp_path: Path):
    """Provides isolated temporary database, storage paths, and chroma directories."""
    db_path = tmp_path / "test_atlas.db"
    chroma_dir = tmp_path / "test_chroma"
    storage_root = tmp_path / "data"

    database.initialize_database(db_path=db_path)

    return {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "chroma_dir": chroma_dir,
        "storage_root": storage_root
    }


# -------------------------------------------------------------------------
# Test 1: Database creation and initialization
# -------------------------------------------------------------------------
def test_01_database_creation_and_initialization(temp_env):
    db_path = temp_env["db_path"]
    rows = database.execute_query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
        db_path=db_path
    )
    table_names = {r["name"] for r in rows}
    required_tables = {
        "data_sources", "locations", "datasets", "dataset_areas", "assets",
        "pipeline_runs", "dataset_lineage", "solar_observations",
        "vector_index_records", "analysis_runs", "analysis_inputs", "analysis_results"
    }
    assert required_tables.issubset(table_names), f"Missing tables: {required_tables - table_names}"

    # Verify foreign keys are enabled on connections
    with database.get_connection(db_path) as conn:
        fk_status = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk_status == 1, "Foreign keys must be enabled on every SQLite connection."


# -------------------------------------------------------------------------
# Test 2: Unique ID generation
# -------------------------------------------------------------------------
def test_02_unique_id_generation():
    generators = [
        (generate_source_id, "src_"),
        (generate_location_id, "loc_"),
        (generate_dataset_id, "ds_"),
        (generate_asset_id, "ast_"),
        (generate_observation_id, "obs_"),
        (generate_vector_record_id, "vec_"),
        (generate_pipeline_run_id, "run_"),
        (generate_analysis_id, "anl_"),
        (generate_result_id, "res_"),
    ]

    for gen_func, expected_prefix in generators:
        ids = {gen_func() for _ in range(50)}
        assert len(ids) == 50, f"Collision detected in generator {gen_func.__name__}!"
        for single_id in ids:
            assert single_id.startswith(expected_prefix), f"ID '{single_id}' must start with prefix '{expected_prefix}'"


# -------------------------------------------------------------------------
# Test 3: Foreign-key validation
# -------------------------------------------------------------------------
def test_03_foreign_key_validation(temp_env):
    db_path = temp_env["db_path"]
    # Attempt inserting a dataset with non-existent source_id and location_id
    with pytest.raises(sqlite3.IntegrityError):
        database.insert_dataset(
            dataset_id="ds_fake_123",
            source_id="src_non_existent",
            location_id="loc_non_existent",
            name="Orphan Dataset",
            data_type="solar_irradiance",
            format_type="csv",
            db_path=db_path
        )


# -------------------------------------------------------------------------
# Test 4: Dataset registration
# -------------------------------------------------------------------------
def test_04_dataset_registration(temp_env):
    csv_file = gis_processor.create_synthetic_solar_csv(
        temp_env["tmp_path"] / "demo_solar.csv",
        center_lat=28.80,
        center_lon=79.02
    )
    res = ingest_dataset(
        source_file_path=csv_file,
        dataset_name="Rampur Survey 2026",
        data_type="solar_irradiance",
        location_name="Rampur",
        center_lat=28.80,
        center_lon=79.02,
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"],
        storage_root=temp_env["storage_root"]
    )
    assert res["status"] == "ready"
    assert res["dataset_id"].startswith("ds_")
    assert res["asset_id"].startswith("ast_")
    assert Path(res["storage_path"]).is_file()

    # Check status in SQLite
    ds_row = database.get_dataset(res["dataset_id"], db_path=temp_env["db_path"])
    assert ds_row is not None
    assert ds_row["status"] == "ready"
    assert ds_row["name"] == "Rampur Survey 2026"


# -------------------------------------------------------------------------
# Test 5: Duplicate ingestion and idempotent retries
# -------------------------------------------------------------------------
def test_05_duplicate_ingestion_and_idempotency(temp_env):
    csv_file = gis_processor.create_synthetic_solar_csv(
        temp_env["tmp_path"] / "idempotent_test.csv",
        center_lat=28.80,
        center_lon=79.02
    )

    # First ingestion
    first = ingest_dataset(
        source_file_path=csv_file,
        dataset_name="Idempotent Test Survey",
        data_type="solar_irradiance",
        location_name="Rampur",
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"],
        storage_root=temp_env["storage_root"]
    )
    assert first["status"] == "ready"

    # Second ingestion with identical parameters
    second = ingest_dataset(
        source_file_path=csv_file,
        dataset_name="Idempotent Test Survey",
        data_type="solar_irradiance",
        location_name="Rampur",
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"],
        storage_root=temp_env["storage_root"]
    )
    assert second["status"] == "already_ingested"
    assert second["dataset_id"] == first["dataset_id"]

    # Verify no duplicate datasets in SQLite
    rows = database.execute_query(
        "SELECT COUNT(*) AS cnt FROM datasets WHERE name = 'Idempotent Test Survey'",
        db_path=temp_env["db_path"]
    )
    assert rows[0]["cnt"] == 1, "Duplicate logical datasets must not be created."


# -------------------------------------------------------------------------
# Test 6: File checksum verification
# -------------------------------------------------------------------------
def test_06_file_checksum_verification(temp_env):
    csv_file = gis_processor.create_synthetic_solar_csv(
        temp_env["tmp_path"] / "checksum_test.csv"
    )
    initial_hash = calculate_sha256(csv_file)

    res = ingest_dataset(
        source_file_path=csv_file,
        dataset_name="Integrity Dataset",
        data_type="solar_irradiance",
        location_name="Rampur",
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"],
        storage_root=temp_env["storage_root"]
    )
    assert res["checksum_sha256"] == initial_hash

    # Tamper with the stored file
    stored_path = Path(res["storage_path"])
    with open(stored_path, "a") as f:
        f.write("\nTAMPERED_RECORD,0,0,0,0,0,0,0,0,0")

    # Pipeline B retrieval should detect the tampered checksum
    retrieval = retrieve_datasets(
        RetrievalRequest(location_name="Rampur", data_type="solar_irradiance"),
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"],
        verify_checksums=True
    )
    assert retrieval["matched_dataset_count"] == 0, "Tampered dataset must be excluded."
    assert any("Checksum mismatch" in w for w in retrieval["warnings"])


# -------------------------------------------------------------------------
# Test 7: ChromaDB indexing and retrieval
# -------------------------------------------------------------------------
def test_07_chromadb_indexing_and_retrieval(temp_env):
    csv_file = gis_processor.create_synthetic_solar_csv(
        temp_env["tmp_path"] / "chroma_test.csv"
    )
    res = ingest_dataset(
        source_file_path=csv_file,
        dataset_name="Chroma High Irradiance Area",
        data_type="solar_irradiance",
        location_name="Rampur",
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"],
        storage_root=temp_env["storage_root"]
    )

    col = get_chroma_collection(chroma_dir=temp_env["chroma_dir"])
    q_res = col.query(query_texts=["solar irradiance Rampur"], n_results=1)

    assert len(q_res["ids"][0]) > 0
    assert q_res["metadatas"][0][0]["dataset_id"] == res["dataset_id"]


# -------------------------------------------------------------------------
# Test 8: Retrieval with different location and date filters
# -------------------------------------------------------------------------
def test_08_retrieval_location_and_date_filters(temp_env):
    csv_rampur = gis_processor.create_synthetic_solar_csv(
        temp_env["tmp_path"] / "rampur.csv", center_lat=28.80, center_lon=79.02
    )
    csv_jaipur = gis_processor.create_synthetic_solar_csv(
        temp_env["tmp_path"] / "jaipur.csv", center_lat=26.91, center_lon=75.78
    )

    ingest_dataset(
        source_file_path=csv_rampur,
        dataset_name="Rampur Solar Survey",
        data_type="solar_irradiance",
        location_name="Rampur",
        center_lat=28.80,
        center_lon=79.02,
        state="Uttar Pradesh",
        acquisition_start="2026-01-01",
        acquisition_end="2026-03-31",
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"],
        storage_root=temp_env["storage_root"]
    )
    ingest_dataset(
        source_file_path=csv_jaipur,
        dataset_name="Jaipur Solar Survey",
        data_type="solar_irradiance",
        location_name="Jaipur",
        center_lat=26.91,
        center_lon=75.78,
        state="Rajasthan",
        acquisition_start="2026-05-01",
        acquisition_end="2026-07-31",
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"],
        storage_root=temp_env["storage_root"]
    )

    # 1. Query for Rampur only
    res_rampur = retrieve_datasets(
        RetrievalRequest(location_name="Rampur"),
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"]
    )
    assert res_rampur["matched_dataset_count"] == 1
    assert res_rampur["datasets"][0]["location"]["name"] == "Rampur"

    # 2. Query for date range in January 2026 (should match Rampur, not Jaipur)
    res_date = retrieve_datasets(
        RetrievalRequest(start_date="2026-01-15", end_date="2026-02-15"),
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"]
    )
    assert res_date["matched_dataset_count"] == 1
    assert res_date["datasets"][0]["name"] == "Rampur Solar Survey"


# -------------------------------------------------------------------------
# Test 9: Prevention of cross-dataset record mixing
# -------------------------------------------------------------------------
def test_09_prevention_of_cross_dataset_mixing(temp_env):
    f1 = gis_processor.create_synthetic_solar_csv(temp_env["tmp_path"] / "f1.csv", center_lat=28.80, center_lon=79.02, num_points=12)
    f2 = gis_processor.create_synthetic_solar_csv(temp_env["tmp_path"] / "f2.csv", center_lat=28.85, center_lon=79.10, num_points=6)

    r1 = ingest_dataset(f1, "Dataset Alpha", "solar_irradiance", location_name="Rampur", version="v1.0", db_path=temp_env["db_path"], chroma_dir=temp_env["chroma_dir"], storage_root=temp_env["storage_root"])
    r2 = ingest_dataset(f2, "Dataset Beta", "solar_irradiance", location_name="Rampur", version="v2.0", db_path=temp_env["db_path"], chroma_dir=temp_env["chroma_dir"], storage_root=temp_env["storage_root"])

    # Query observations specifically for Alpha
    obs_alpha = database.execute_query(
        "SELECT * FROM solar_observations WHERE dataset_id = ?",
        (r1["dataset_id"],),
        db_path=temp_env["db_path"]
    )
    assert len(obs_alpha) > 0
    for obs in obs_alpha:
        assert obs["dataset_id"] == r1["dataset_id"]
        assert obs["dataset_id"] != r2["dataset_id"]


# -------------------------------------------------------------------------
# Test 10: Invalid or missing file handling
# -------------------------------------------------------------------------
def test_10_invalid_and_missing_file_handling(temp_env):
    # Empty file
    empty_file = temp_env["tmp_path"] / "corrupt.csv"
    empty_file.write_text("")

    res = ingest_dataset(
        source_file_path=empty_file,
        dataset_name="Corrupt Empty File",
        data_type="solar_irradiance",
        location_name="Rampur",
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"],
        storage_root=temp_env["storage_root"]
    )
    assert res["status"] == "quarantined"
    assert "empty" in res["reason"].lower()
    assert Path(res["storage_path"]).is_file()
    assert "quarantine" in res["storage_path"].lower()


# -------------------------------------------------------------------------
# Test 11: Failed indexing and retry behavior
# -------------------------------------------------------------------------
def test_11_failed_indexing_and_retry_behavior(temp_env):
    csv_file = gis_processor.create_synthetic_solar_csv(temp_env["tmp_path"] / "retry_test.csv")
    res = ingest_dataset(
        csv_file, "Retry Dataset", "solar_irradiance", location_name="Rampur",
        db_path=temp_env["db_path"], chroma_dir=temp_env["chroma_dir"], storage_root=temp_env["storage_root"]
    )
    ds_id = res["dataset_id"]

    # Simulate an index drop/failure by resetting dataset status to 'stored'
    database.update_dataset_status(ds_id, "stored", db_path=temp_env["db_path"])

    # Rebuild index
    retry_res = rebuild_or_retry_indexing(ds_id, db_path=temp_env["db_path"], chroma_dir=temp_env["chroma_dir"])
    assert retry_res["status"] == "ready"
    assert retry_res["chunks_indexed"] > 0

    ds_after = database.get_dataset(ds_id, db_path=temp_env["db_path"])
    assert ds_after["status"] == "ready"


# -------------------------------------------------------------------------
# Test 12: Solar calculation correctness
# -------------------------------------------------------------------------
def test_12_solar_calculation_correctness():
    assumptions = SolarAnalysisAssumptions(
        roof_area_m2=100.0,
        usable_area_percent=80.0,  # 80 m² usable
        kwp_per_10m2=1.0,          # 8.0 kWp capacity
        performance_ratio=0.75,
        tariff_inr_per_kwh=6.5
    )
    daily_ghi = 5.0  # 5.0 kWh/m²/day

    calc = calculate_solar_potential(assumptions, daily_ghi)

    expected_usable = 80.0
    expected_capacity = 8.0
    expected_daily_kwh = 8.0 * 5.0 * 0.75  # 30.0 kWh/day
    expected_annual_kwh = 30.0 * 365.0      # 10950.0 kWh/year
    expected_co2 = 10950.0 * INDIA_GRID_EMISSION_FACTOR_KG_PER_KWH  # ~7840.2 kg
    expected_savings = 10950.0 * 6.5         # 71175.0 INR/year

    assert calc["usable_area_m2"] == pytest.approx(expected_usable, rel=1e-3)
    assert calc["capacity_kwp"] == pytest.approx(expected_capacity, rel=1e-3)
    assert calc["daily_yield_kwh"] == pytest.approx(expected_daily_kwh, rel=1e-3)
    assert calc["annual_yield_kwh"] == pytest.approx(expected_annual_kwh, rel=1e-3)
    assert calc["avoided_co2_kg_year"] == pytest.approx(expected_co2, rel=1e-3)
    assert calc["annual_savings_inr"] == pytest.approx(expected_savings, rel=1e-3)


# -------------------------------------------------------------------------
# Test 13: Analysis results linked to correct input datasets
# -------------------------------------------------------------------------
def test_13_analysis_results_linked_to_inputs(temp_env):
    csv_file = gis_processor.create_synthetic_solar_csv(temp_env["tmp_path"] / "analysis_link.csv")
    ing = ingest_dataset(
        csv_file, "Analysis Source Survey", "solar_irradiance", location_name="Rampur",
        db_path=temp_env["db_path"], chroma_dir=temp_env["chroma_dir"], storage_root=temp_env["storage_root"]
    )
    ret = retrieve_datasets(
        RetrievalRequest(location_name="Rampur"),
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"]
    )

    analysis = run_solar_analysis(
        input_datasets=ret["datasets"],
        assumptions=SolarAnalysisAssumptions(roof_area_m2=150.0),
        db_path=temp_env["db_path"]
    )

    lineage = database.get_analysis_lineage(analysis["analysis_id"], db_path=temp_env["db_path"])
    assert len(lineage["inputs"]) == 1
    assert lineage["inputs"][0]["dataset_id"] == ing["dataset_id"]
    assert len(lineage["results"]) >= 6


# -------------------------------------------------------------------------
# Test 14: No-result retrieval behavior
# -------------------------------------------------------------------------
def test_14_no_result_retrieval_behavior(temp_env):
    # Query for a location that definitely does not exist
    ret = retrieve_datasets(
        RetrievalRequest(location_name="AntarcticaNonExistentZoneXYZ"),
        db_path=temp_env["db_path"],
        chroma_dir=temp_env["chroma_dir"]
    )
    assert ret["matched_dataset_count"] == 0
    assert len(ret["datasets"]) == 0
    assert len(ret["warnings"]) > 0
    assert "No datasets matched" in ret["warnings"][0]
