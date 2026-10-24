"""
demo.py - Reproducible End-to-End Pipeline Demonstration
Solar Atlas: Space Hackathon AI for Solar Energy Potential Mapping

Demonstrates:
1. Generating a synthetic solar demonstration dataset.
2. Ingestion (Pipeline A): Unique IDs, checksum, raw storage, ChromaDB indexing.
3. Duplicate Ingestion: Idempotency check preventing duplicate records.
4. Retrieval (Pipeline B): Multi-criteria filter + semantic vector search + physical disk verification.
5. Solar Analysis: Physics-grounded solar calculations with CEA grid emission factor.
6. Authoritative Lineage: Verifying database relations connecting inputs to results.
"""

from __future__ import annotations
import json
from pathlib import Path
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.database import (
    initialize_database,
    execute_query,
    get_dataset,
    get_assets_by_dataset,
    get_analysis_lineage
)
from backend.gis_processor import (
    create_synthetic_solar_csv,
    create_synthetic_solar_raster,
    create_synthetic_building_geojson,
    BhuvanAdapterInterface,
    VEDASAdapterInterface
)
from backend.ingestion_pipeline import ingest_dataset
from backend.retrieval_pipeline import RetrievalRequest, retrieve_datasets
from backend.solar_backend import SolarAnalysisAssumptions, run_solar_analysis


def print_banner(text: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def run_demonstration() -> None:
    print_banner("SOLAR ATLAS — END-TO-END DEMONSTRATION")
    print("Prototype Architecture: SQLite + ChromaDB + Local File Storage")
    print("Study Area: Rampur, Uttar Pradesh (Coordinates: 28.80° N, 79.02° E)\n")

    # Ensure database is initialized
    initialize_database()

    # Step 1: Create small synthetic demo data
    print("--> STEP 1: Generating Synthetic Demonstration Files...")
    sample_dir = PROJECT_ROOT / "data" / "sample_inputs"
    sample_dir.mkdir(parents=True, exist_ok=True)

    csv_file = create_synthetic_solar_csv(
        sample_dir / "rampur_solar_observations_2026.csv",
        center_lat=28.80,
        center_lon=79.02,
        num_points=12
    )
    print(f"    [+] Created synthetic solar CSV: {csv_file}")

    raster_file = create_synthetic_solar_raster(
        sample_dir / "rampur_solar_raster_2026.tif",
        center_lat=28.80,
        center_lon=79.02
    )
    print(f"    [+] Created synthetic solar GeoTIFF raster: {raster_file}")

    # Step 2: Pipeline A - Ingestion
    print_banner("STEP 2: PIPELINE A — DATA INGESTION & REGISTRATION")
    print("Ingesting synthetic solar observation dataset into authoritative catalogue...")

    ingest_result = ingest_dataset(
        source_file_path=csv_file,
        dataset_name="Rampur Solar Irradiance Survey 2026",
        data_type="solar_irradiance",
        source_name="ISRO Space Hackathon Demo Provider",
        provider_type="synthetic_demo",
        location_name="Rampur",
        center_lat=28.80,
        center_lon=79.02,
        state="Uttar Pradesh",
        country="India",
        version="v1.0",
        acquisition_start="2026-03-01",
        acquisition_end="2026-03-31"
    )

    print(f"    Status:                {ingest_result['status']}")
    print(f"    Dataset ID:            {ingest_result.get('dataset_id')}")
    print(f"    Asset ID:              {ingest_result.get('asset_id')}")
    print(f"    Storage Path:          {ingest_result.get('storage_path')}")
    print(f"    SHA-256 Checksum:      {ingest_result.get('checksum_sha256')}")
    print(f"    Chroma Chunks Indexed: {ingest_result.get('chunks_indexed')}")
    print(f"    Pipeline Run ID:       {ingest_result.get('pipeline_run_id')}")

    target_dataset_id = ingest_result["dataset_id"]

    # Step 3: Test Idempotency (Duplicate Ingestion)
    print_banner("STEP 3: IDEMPOTENCY & DUPLICATE PREVENTION")
    print("Attempting to re-ingest the exact same dataset...")

    dup_result = ingest_dataset(
        source_file_path=csv_file,
        dataset_name="Rampur Solar Irradiance Survey 2026",
        data_type="solar_irradiance",
        location_name="Rampur",
        version="v1.0"
    )
    print(f"    Duplicate Ingestion Status:  {dup_result['status']}")
    print(f"    Message:                     {dup_result.get('message')}")
    print(f"    Preserved Existing ID:       {dup_result.get('dataset_id')}")
    assert dup_result["dataset_id"] == target_dataset_id, "Idempotency failed: generated a different dataset ID!"
    print("    [PASS] Idempotency confirmed: No duplicate records created.")

    # Step 4: Pipeline B - Retrieval
    print_banner("STEP 4: PIPELINE B — FILTERED DATA RETRIEVAL")
    print("Querying datasets for location 'Rampur' with semantic search 'solar irradiance survey'...")

    query = RetrievalRequest(
        location_name="Rampur",
        data_type="solar_irradiance",
        semantic_query="solar irradiance survey",
        min_quality_score=0.8
    )
    retrieval_res = retrieve_datasets(query)

    print(f"    Datasets Matched:      {retrieval_res['matched_dataset_count']}")
    if retrieval_res["warnings"]:
        print(f"    Retrieval Warnings:    {retrieval_res['warnings']}")

    for ds in retrieval_res["datasets"]:
        print(f"\n    Dataset: {ds['name']} (ID: {ds['dataset_id']})")
        print(f"      Location: {ds['location']['name']}, {ds['location']['state']}")
        print(f"      Status:   {ds['status']} (Verified in SQLite catalogue)")
        print(f"      Assets:   {len(ds['assets'])} verified file(s) on disk")
        for ast in ds["assets"]:
            print(f"        -> Asset ID: {ast['asset_id']}")
            print(f"        -> Filename: {ast['original_filename']}")
            print(f"        -> Storage:  {ast['storage_path']}")
            print(f"        -> Checksum: {ast['checksum_sha256'][:16]}...")
        obs = ds["observations_summary"]
        print(f"      Solar Observations: {obs['observation_count']} points (Mean GHI: {obs['mean_ghi_kwh_m2_day']} kWh/m²/day)")

    # Step 5: Solar Analysis Backend
    print_banner("STEP 5: SOLAR ANALYSIS BACKEND EXECUTION")
    print("Running physics-grounded engineering solar yield calculations...")

    assumptions = SolarAnalysisAssumptions(
        roof_area_m2=120.0,
        usable_area_percent=70.0,
        kwp_per_10m2=1.0,
        performance_ratio=0.75,
        tariff_inr_per_kwh=7.0
    )

    analysis_res = run_solar_analysis(
        input_datasets=retrieval_res["datasets"],
        assumptions=assumptions
    )

    results = analysis_res["results"]
    print(f"    Analysis Run ID:         {analysis_res['analysis_id']}")
    print(f"    Algorithm:               {analysis_res['algorithm']}")
    print(f"    Demonstration Flag:      {analysis_res['is_demonstration']}")
    print(f"    Usable Rooftop Area:     {results['usable_area_m2']} m²")
    print(f"    Estimated PV Capacity:   {results['capacity_kwp']} kWp")
    print(f"    Daily Solar GHI Input:   {results['daily_ghi_kwh_m2_day']} kWh/m²/day")
    print(f"    Daily Electricity Yield: {results['daily_yield_kwh']} kWh/day")
    print(f"    Annual Electricity Yield:{results['annual_yield_kwh']} kWh/year")
    print(f"    CO2 Emissions Avoided:   {results['avoided_co2_kg_year']} kg CO2/year (CEA India Baseline)")
    print(f"    Annual Energy Value:     ₹{results['annual_savings_inr']:,.2f}")

    # Step 6: Verify Authoritative Lineage in SQLite
    print_banner("STEP 6: AUTHORITATIVE LINEAGE VERIFICATION")
    lineage = get_analysis_lineage(analysis_res["analysis_id"])

    print(f"    Analysis ID in SQLite:    {lineage['run']['analysis_id']}")
    print(f"    Linked Input Datasets:    {len(lineage['inputs'])}")
    for inp in lineage["inputs"]:
        print(f"      [Input] Dataset ID: {inp['dataset_id']} ({inp['dataset_name']}) | Role: {inp['role']}")
    print(f"    Saved Result Metrics:     {len(lineage['results'])}")
    for res in lineage["results"]:
        print(f"      [Result] {res['metric_name']}: {res['numerical_value']} {res['unit']} (Conf: {res['confidence_score']})")

    # Step 7: Provider Adapter Verification
    print_banner("STEP 7: PROVIDER ADAPTERS (BHUVAN & VEDAS)")
    bhuvan = BhuvanAdapterInterface()
    vedas = VEDASAdapterInterface()
    print("    ISRO Bhuvan Adapter Status:", bhuvan.validate_connection()["message"])
    print("    SAC VEDAS Adapter Status:  ", vedas.validate_connection()["message"])

    print_banner("DEMONSTRATION COMPLETED SUCCESSFULLY")
    print("All 3 storage layers (SQLite, ChromaDB, File Storage) and both pipelines are working.\n")


if __name__ == "__main__":
    run_demonstration()
