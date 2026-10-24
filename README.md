# Solar Atlas — Space Hackathon Prototype

**AI for Solar Energy Potential Mapping using Remote Sensing & GIS (Bhuvan & VEDAS)**

Solar Atlas is an engineering prototype designed for a Space Hackathon. It implements a 3-layer storage architecture (authoritative SQLite metadata catalogue, ChromaDB vector store, and physical GIS file storage) with dual independent pipelines for reliable data ingestion, strict cross-verified retrieval, and physics-grounded solar calculations.

---

## 1. System Architecture

The application separates concerns across three dedicated storage tiers and two distinct pipelines:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Streamlit Frontend (app.py)                     │
└─────────────────────────────────┬──────────────────────────────────────┘
                                  │
         ┌────────────────────────┴────────────────────────┐
         ▼                                                 ▼
┌──────────────────────────────┐        ┌────────────────────────────────┐
│ Pipeline A: Ingestion        │        │ Pipeline B: Retrieval          │
│ (ingestion_pipeline.py)      │        │ (retrieval_pipeline.py)        │
├──────────────────────────────┤        ├────────────────────────────────┤
│ 1. Validate file & format    │        │ 1. SQL filter (status='ready') │
│ 2. Compute SHA-256 Checksum  │        │ 2. ChromaDB semantic search    │
│ 3. Check idempotency key     │        │ 3. 6-point verification        │
│ 4. Store raw / quarantine    │        │ 4. Checksum integrity check    │
│ 5. Register in SQLite        │        │ 5. Return provenanced records  │
│ 6. Index chunks in ChromaDB  │        └────────────────┬───────────────┘
│ 7. Mark status 'ready'       │                         │
└──────────────┬───────────────┘                         ▼
               │                        ┌────────────────────────────────┐
               │                        │ Solar Analysis Backend         │
               │                        │ (solar_backend.py)             │
               │                        ├────────────────────────────────┤
               │                        │ - Physics: GHI, Capacity, PR   │
               │                        │ - Environmental: CEA CO2 factor│
               │                        │ - Full lineage in SQLite       │
               │                        └────────────────┬───────────────┘
               ▼                                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│                           Three Storage Layers                         │
├────────────────────────────┬─────────────────────────────┬─────────────┤
│ 1. SQLite Database         │ 2. ChromaDB Vector Store    │ 3. Files    │
│ (data/solar_atlas.db)      │ (chroma_db/)                │ (data/raw/) │
│ - 12 relational tables     │ - Document chunks           │ - GeoTIFFs  │
│ - Foreign keys enabled     │ - 128-dim embeddings        │ - GeoJSONs  │
│ - Audit lineage & results  │ - Fast semantic ranking     │ - CSVs      │
└────────────────────────────┴─────────────────────────────┴─────────────┘
```

---

## 2. Unique Data-Address System

Data traceability requires distinct identification. Solar Atlas strictly separates:
- **Unique Entity ID:** Collision-resistant UUID identifier (e.g., `ds_514fa9ff329f`).
- **Physical Storage Address:** System URI/path (e.g., `data/raw/ds_514fa9ff329f_solar.csv`).
- **Cryptographic Checksum:** Streaming SHA-256 content hash (e.g., `917dc9d7fbbb81ad...`).
- **Metadata:** Validated JSON text capturing spatial coordinates, CRS, and sensors.

### Entity Identifier Prefixes
| Entity | Prefix Format | Example | Purpose |
| :--- | :--- | :--- | :--- |
| Data Source | `src_<uuid>` | `src_a1b2c3d4e5f6` | Identifies provider (ISRO, VEDAS, Demo) |
| Location | `loc_<uuid>` | `loc_9f8e7d6c5b4a` | Geographic study area and coordinates |
| Dataset Version | `ds_<uuid>` | `ds_514fa9ff329f` | Logical dataset version and status |
| Physical Asset | `ast_<uuid>` | `ast_5afedfac61da` | Specific file on disk |
| Solar Observation | `obs_<uuid>` | `obs_7b8c9d0e1f2a` | Tabular/point physical measurement |
| Vector Record | `vec_<uuid>` | `vec_3e4f5a6b7c8d` | Searchable document chunk in ChromaDB |
| Pipeline Execution | `run_<uuid>` | `run_50cac74eb486` | Ingestion/indexing execution audit |
| Analysis Run | `anl_<uuid>` | `anl_2ed600d7bf99` | Solar calculation execution instance |
| Analysis Result | `res_<uuid>` | `res_8c9d0e1f2a3b` | Individual output metric and unit |

---

## 3. SQLite Database Schema (12 Tables)

All tables enforce foreign keys (`PRAGMA foreign_keys = ON;`), timestamps, check constraints, and indexes in [schema.sql](file:///c:/Users/DELL/Downloads/Solar_Atlas_Prototype/solar_potential_mapper/schema.sql):

1. **`data_sources`**: Authoritative catalogue of remote sensing providers.
2. **`locations`**: Study areas with coordinates, bounding boxes, and CRS.
3. **`datasets`**: Logical datasets with lifecycle states (`registered`, `stored`, `indexing`, `ready`, `quarantined`, `failed`).
4. **`dataset_areas`**: Spatial bounding boxes linking datasets to study locations.
5. **`assets`**: Physical file records with storage paths, MIME types, and SHA-256 checksums.
6. **`pipeline_runs`**: Ingestion and indexing audit logs with execution states.
7. **`dataset_lineage`**: Tracks parent-child transformations between datasets.
8. **`solar_observations`**: Tabular measurements (GHI, DNI, DHI, sunshine hours).
9. **`vector_index_records`**: Maps ChromaDB document chunks back to SQLite assets.
10. **`analysis_runs`**: Solar analysis executions with configuration and assumptions.
11. **`analysis_inputs`**: Lineage links connecting analysis runs to input dataset IDs.
12. **`analysis_results`**: Individual metric outputs (capacity, yield, CO2, financial value).

---

## 4. Dual-Pipeline Operation

### Pipeline A: Data Ingestion (`ingestion_pipeline.py`)
1. Accepts local file (`.tif`, `.geojson`, `.csv`) and metadata.
2. Validates format, CRS, non-empty content, and spatial bounds.
3. If invalid: Copies to `data/quarantine/`, records failure reason, and marks status `quarantined`.
4. If valid: Computes SHA-256 checksum and deterministic idempotency key.
5. If identical dataset was already ingested: Returns existing dataset without creating duplicates.
6. Preserves original file in `data/raw/` with immutable asset ID.
7. Generates descriptive and spatial metadata chunks.
8. Indexes chunks in ChromaDB and records chunk hashes in `vector_index_records`.
9. Marks dataset status as `ready` only after all storage and indexing steps verify.

### Pipeline B: Data Retrieval & Verification (`retrieval_pipeline.py`)
1. Accepts structured query (location, date range, data type, min quality, semantic query).
2. Performs parameterized SQL search for datasets strictly in `ready` status.
3. Queries ChromaDB for semantic similarity if a text query is provided.
4. Performs strict 6-point cross-verification:
   - Validates that dataset exists in SQLite and status is `ready`.
   - Confirms spatial and temporal filter constraints are satisfied.
   - Resolves physical file URI from the asset catalogue.
   - Confirms file exists on disk and is readable.
   - Re-calculates SHA-256 checksum to detect any on-disk tampering.
5. Returns provenanced records with file links and warnings. Never invents data.

---

## 5. Solar Analysis Engine (`solar_backend.py`)

Solar calculations follow physics-grounded engineering formulas:

$$\text{Usable Rooftop Area } (m^2) = \text{Total Area} \times \frac{\text{Usable } \%}{100}$$

$$\text{PV Capacity } (kWp) = \frac{\text{Usable Area}}{10} \times \text{Capacity Rating } (kWp/10m^2)$$

$$\text{Daily Energy } (kWh/day) = \text{Capacity } (kWp) \times \text{Daily GHI } (kWh/m^2/day) \times \text{PR}$$

$$\text{Annual Energy } (kWh/year) = \text{Daily Energy} \times 365$$

$$\text{Avoided } CO_2 \text{ Emissions } (kg/year) = \text{Annual Energy } (kWh) \times 0.716 \text{ kg } CO_2/kWh$$

$$\text{Annual Savings } (₹/year) = \text{Annual Energy } (kWh) \times \text{Tariff } (₹/kWh)$$

*Note: Emission factor $0.716 \text{ kg } CO_2/kWh$ is sourced from the Central Electricity Authority (CEA) of India Baseline Database for the Indian Power Sector.*

---

## 6. Windows Setup & Run Instructions

### Step 1: Open Terminal in VS Code
Open VS Code in the `solar_potential_mapper` directory.

### Step 2: Activate Virtual Environment
PowerShell:
```powershell
.venv\Scripts\Activate.ps1
```
*(If PowerShell blocks script execution, run `.venv\Scripts\activate.bat` in Command Prompt).*

### Step 3: Run the Automated Tests
Run the 14-point test suite with pytest:
```powershell
python -m pytest -v tests/test_solar_atlas.py
```
*(All 14 tests run on isolated temporary databases and temporary directories).*

### Step 4: Run the Standalone Demonstration Script
```powershell
python scripts/demo.py
```
This script will ingest a synthetic dataset, demonstrate idempotency, retrieve with verification, run solar calculations, and inspect database lineage.

### Step 5: Launch the Streamlit Web Application
```powershell
python -m streamlit run frontend/app.py
```
Open `http://localhost:8501` in your browser. (Alternatively, run `scripts\run_on_windows.bat` for one-click launch).

---

## 7. Folder Structure

```
solar_potential_mapper/
│
├── frontend/                  # Streamlit User Interface Package
│   ├── __init__.py            # Package root for frontend components
│   └── app.py                 # Interactive UI with Calculator, GIS Map, & Pipeline UIs
│
├── backend/                   # Modular Solar Atlas Backend Package
│   ├── __init__.py            # Package root exposing backend modules and functions
│   ├── database.py            # Authoritative SQLite manager with PRAGMA foreign_keys
│   ├── schema.sql             # 12-table relational DDL with constraints and indexes
│   ├── id_utils.py            # UUID-based ID generation, checksums, & idempotency keys
│   ├── ingestion_pipeline.py  # Pipeline A: Ingestion, validation, quarantine, ChromaDB
│   ├── retrieval_pipeline.py  # Pipeline B: Multi-criteria filter, semantic search, 6-point check
│   ├── solar_backend.py       # Physics-grounded solar yield and CEA CO2 calculations
│   └── gis_processor.py       # Raster/Vector inspectors, synthetic data generators, adapters
│
├── scripts/                   # Executable Utilities and Launchers
│   ├── demo.py                # Standalone reproducible demonstration script
│   └── run_on_windows.bat     # Windows one-click launcher
│
├── docs/                      # Documentation and Supplementary Guides
│   └── README_HINGLISH.md     # Quickstart setup in Hinglish
│
├── data/                      # Physical File & Relational Storage Layer
│   ├── raw/                   # Preserved verified physical input files
│   ├── quarantine/            # Corrupt or invalid files with failure logs
│   ├── sample_inputs/         # Generated synthetic demonstration datasets
│   └── solar_atlas.db         # Authoritative SQLite database
├── chroma_db/                 # ChromaDB persistent vector database
├── tests/                     # Automated Test Scenarios
│   └── test_solar_atlas.py    # 14 mandatory automated pytest scenarios
├── requirements.txt           # Verified Python package dependencies
├── README.md                  # Comprehensive engineering documentation
└── .gitignore                 # Excludes databases, ChromaDB files, and raw storage
```

---

## 8. Limitations & Roadmap for Live Bhuvan / VEDAS Data

### Current Limitations:
- Real satellite data access requires official credentials from ISRO/NRSC (Bhuvan) and SAC-ISRO (VEDAS).
- This prototype uses clearly labelled **DEMONSTRATION DATA** to guarantee privacy, reproducibility, and compliance with hackathon rules.
- Calculations are planning estimates; actual installations require on-site shading analysis and electrical layout surveys.

### Steps for Future Live Provider Integration:
1. **Credentials:** Register at Bhuvan Open Data Archive (BODA) and SAC VEDAS to obtain authorized API tokens.
2. **Provider Adapters:** Replace stubs in `gis_processor.py` (`BhuvanAdapterInterface` and `VEDASAdapterInterface`) by supplying your live API key to connect to authorized WMS/WCS endpoints.
3. **Building Footprints:** Integrate a trained Deep Learning segmentation model (e.g., Mask R-CNN or SAM) to extract real rooftop polygons from Cartosat/LISS-IV high-resolution imagery.
4. **Validation:** Calibrate estimates against real ground-truth rooftop solar meter data.
