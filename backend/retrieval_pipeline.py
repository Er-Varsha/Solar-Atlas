"""
retrieval_pipeline.py - Pipeline B: Multi-Filter Data Retrieval & Cross-Verification
Solar Atlas: Space Hackathon AI for Solar Energy Potential Mapping

Responsibilities:
1. Accept structured filter requests (location, date range, data type, source, quality, semantic query).
2. Filter eligible datasets via SQL ensuring status is strictly 'ready'.
3. Perform ChromaDB similarity search when a semantic query is provided.
4. Cross-verify every candidate against SQLite catalogue:
   - Enforce location, date, type, and source constraints.
   - Resolve physical storage URIs from asset records.
   - Verify that original files exist on disk and checksums match.
5. Prevent cross-dataset contamination and never invent synthetic data.
6. Return structured response with audit provenance and warnings.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from .database import (
        execute_query,
        get_assets_by_dataset,
        get_dataset
    )
    from .id_utils import calculate_sha256
    from .ingestion_pipeline import get_chroma_collection
except (ImportError, ValueError):
    from database import (
        execute_query,
        get_assets_by_dataset,
        get_dataset
    )
    from id_utils import calculate_sha256
    from ingestion_pipeline import get_chroma_collection


class RetrievalRequest:
    """Structured search query parameter container."""
    def __init__(
        self,
        location_name: Optional[str] = None,
        min_lat: Optional[float] = None,
        max_lat: Optional[float] = None,
        min_lon: Optional[float] = None,
        max_lon: Optional[float] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        data_type: Optional[str] = None,
        source_name: Optional[str] = None,
        min_quality_score: float = 0.0,
        semantic_query: Optional[str] = None,
        max_results: int = 10
    ):
        self.location_name = location_name.strip() if location_name else None
        self.min_lat = min_lat
        self.max_lat = max_lat
        self.min_lon = min_lon
        self.max_lon = max_lon
        self.start_date = start_date.strip() if start_date else None
        self.end_date = end_date.strip() if end_date else None
        self.data_type = data_type.strip() if data_type else None
        self.source_name = source_name.strip() if source_name else None
        self.min_quality_score = min_quality_score
        self.semantic_query = semantic_query.strip() if semantic_query else None
        self.max_results = max_results

    def to_dict(self) -> Dict[str, Any]:
        return {
            "location_name": self.location_name,
            "bounds": [self.min_lon, self.min_lat, self.max_lon, self.max_lat] if self.min_lat is not None else None,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "data_type": self.data_type,
            "source_name": self.source_name,
            "min_quality_score": self.min_quality_score,
            "semantic_query": self.semantic_query,
            "max_results": self.max_results
        }


def retrieve_datasets(
    request: RetrievalRequest,
    db_path: Optional[Union[str, Path]] = None,
    chroma_dir: Optional[Union[str, Path]] = None,
    verify_checksums: bool = True
) -> Dict[str, Any]:
    """
    Execute Pipeline B retrieval with strict SQL constraints, ChromaDB semantic ranking,
    and physical disk asset verification.
    """
    warnings: List[str] = []

    # Step 1: Build parameterized SQL query for candidate datasets
    sql = """
        SELECT
            d.dataset_id, d.name AS dataset_name, d.data_type, d.format, d.version,
            d.status, d.acquisition_date_start, d.acquisition_date_end,
            d.spatial_resolution_meters, d.quality_score, d.is_demonstration,
            d.metadata_json AS dataset_meta,
            l.location_id, l.name AS location_name, l.state, l.country,
            l.center_lat, l.center_lon,
            s.source_id, s.name AS source_name, s.provider_type
        FROM datasets d
        JOIN locations l ON d.location_id = l.location_id
        JOIN data_sources s ON d.source_id = s.source_id
        WHERE d.status = 'ready'
    """
    params: List[Any] = []

    if request.location_name:
        sql += " AND (LOWER(l.name) LIKE ? OR LOWER(l.state) LIKE ?)"
        loc_term = f"%{request.location_name.lower()}%"
        params.extend([loc_term, loc_term])

    if request.data_type:
        sql += " AND LOWER(d.data_type) = ?"
        params.append(request.data_type.lower())

    if request.source_name:
        sql += " AND LOWER(s.name) LIKE ?"
        params.append(f"%{request.source_name.lower()}%")

    if request.min_quality_score > 0.0:
        sql += " AND d.quality_score >= ?"
        params.append(request.min_quality_score)

    # Date range overlap filtering
    if request.start_date:
        sql += " AND (d.acquisition_date_end >= ? OR d.acquisition_date_end IS NULL)"
        params.append(request.start_date)

    if request.end_date:
        sql += " AND (d.acquisition_date_start <= ? OR d.acquisition_date_start IS NULL)"
        params.append(request.end_date)

    # Bounding box spatial overlap filtering
    if None not in (request.min_lat, request.max_lat, request.min_lon, request.max_lon):
        sql += """
            AND EXISTS (
                SELECT 1 FROM dataset_areas da
                WHERE da.dataset_id = d.dataset_id
                  AND da.min_lon <= ? AND da.max_lon >= ?
                  AND da.min_lat <= ? AND da.max_lat >= ?
            )
        """
        params.extend([request.max_lon, request.min_lon, request.max_lat, request.min_lat])

    sql += " ORDER BY d.created_at DESC"
    candidates = execute_query(sql, tuple(params), db_path=db_path)

    if not candidates:
        return {
            "query": request.to_dict(),
            "matched_dataset_count": 0,
            "datasets": [],
            "warnings": ["No datasets matched the requested location, temporal, or quality filters."]
        }

    candidate_map = {c["dataset_id"]: c for c in candidates}
    ranked_dataset_ids = list(candidate_map.keys())

    # Step 2: Semantic search with ChromaDB if query provided
    if request.semantic_query:
        try:
            col = get_chroma_collection(chroma_dir=chroma_dir)
            chroma_res = col.query(
                query_texts=[request.semantic_query],
                n_results=min(request.max_results * 2, 25)
            )

            matched_ids = []
            if chroma_res and chroma_res.get("metadatas"):
                for meta_list in chroma_res["metadatas"]:
                    for item in meta_list:
                        if item and "dataset_id" in item:
                            ds_id = item["dataset_id"]
                            # Only include if dataset also passed strict SQL filtering
                            if ds_id in candidate_map and ds_id not in matched_ids:
                                matched_ids.append(ds_id)

            if matched_ids:
                ranked_dataset_ids = matched_ids
            else:
                warnings.append(
                    f"Semantic query '{request.semantic_query}' had low vector similarity with SQL matches; "
                    "showing SQL-matched datasets."
                )
        except Exception as e:
            warnings.append(f"Vector search bypassed due to index notice: {str(e)}")

    # Step 3: Verify assets, disk existence, and checksums
    verified_results = []
    for ds_id in ranked_dataset_ids[:request.max_results]:
        cand = candidate_map[ds_id]
        assets = get_assets_by_dataset(ds_id, db_path=db_path)

        verified_assets = []
        all_assets_valid = True

        for asset in assets:
            storage_path = Path(asset["storage_path"])
            if not storage_path.is_file():
                all_assets_valid = False
                warnings.append(f"Missing physical file for asset {asset['asset_id']} at {storage_path}")
                continue

            if verify_checksums:
                try:
                    current_checksum = calculate_sha256(storage_path)
                    if current_checksum != asset["checksum_sha256"]:
                        all_assets_valid = False
                        warnings.append(
                            f"Integrity check failed: Checksum mismatch for asset {asset['asset_id']}. "
                            f"Expected {asset['checksum_sha256']}, got {current_checksum}"
                        )
                        continue
                except Exception as err:
                    all_assets_valid = False
                    warnings.append(f"Could not compute checksum for {storage_path}: {str(err)}")
                    continue

            verified_assets.append({
                "asset_id": asset["asset_id"],
                "original_filename": asset["original_filename"],
                "storage_uri": asset["storage_uri"],
                "storage_path": asset["storage_path"],
                "checksum_sha256": asset["checksum_sha256"],
                "file_size_bytes": asset["file_size_bytes"],
                "mime_type": asset["mime_type"],
                "asset_type": asset["asset_type"],
                "crs": asset["crs"],
                "resolution_meters": asset["resolution_meters"]
            })

        if not all_assets_valid or not verified_assets:
            warnings.append(f"Dataset {ds_id} excluded due to physical asset verification failure.")
            continue

        # Fetch structured observations if any
        obs_rows = execute_query(
            """
            SELECT observation_id, latitude, longitude, observation_time,
                   ghi_kwh_m2_day, dni_kwh_m2_day, sunshine_duration_hours,
                   rooftop_area_m2, suitability_score
            FROM solar_observations
            WHERE dataset_id = ?
            LIMIT 50
            """,
            (ds_id,),
            db_path=db_path
        )

        mean_ghi = None
        if obs_rows:
            valid_ghis = [r["ghi_kwh_m2_day"] for r in obs_rows if r["ghi_kwh_m2_day"] is not None]
            mean_ghi = round(sum(valid_ghis) / len(valid_ghis), 2) if valid_ghis else None

        verified_results.append({
            "dataset_id": ds_id,
            "name": cand["dataset_name"],
            "data_type": cand["data_type"],
            "format": cand["format"],
            "version": cand["version"],
            "status": cand["status"],
            "quality_score": cand["quality_score"],
            "is_demonstration": bool(cand["is_demonstration"]),
            "source": {
                "source_id": cand["source_id"],
                "name": cand["source_name"],
                "provider_type": cand["provider_type"]
            },
            "location": {
                "location_id": cand["location_id"],
                "name": cand["location_name"],
                "state": cand["state"],
                "country": cand["country"],
                "center_lat": cand["center_lat"],
                "center_lon": cand["center_lon"]
            },
            "temporal_coverage": {
                "start": cand["acquisition_date_start"],
                "end": cand["acquisition_date_end"]
            },
            "assets": verified_assets,
            "observations_summary": {
                "observation_count": len(obs_rows),
                "mean_ghi_kwh_m2_day": mean_ghi,
                "sample_points": obs_rows[:5]
            }
        })

    return {
        "query": request.to_dict(),
        "matched_dataset_count": len(verified_results),
        "datasets": verified_results,
        "warnings": warnings
    }
