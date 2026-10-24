"""
solar_backend.py - Physics-Grounded Solar Potential Analysis Engine
Solar Atlas: Space Hackathon AI for Solar Energy Potential Mapping

Responsibilities:
1. Receive validated datasets and assets from the retrieval pipeline.
2. Execute physics-grounded solar calculations with explicit units and documented formulas.
3. Distinguish Global Horizontal Irradiance (GHI, kWh/m²/day) from sunshine hours.
4. Compute capacity (kWp), daily/annual yield (kWh), avoided CO2 (kg/yr), and financial savings.
5. Record complete lineage in SQLite (analysis_runs, analysis_inputs, analysis_results).
6. Provide transparent, clearly labelled demonstration vs satellite-derived indicators.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from .database import (
        execute_query,
        get_analysis_lineage,
        insert_analysis_input,
        insert_analysis_result,
        insert_analysis_run,
        update_analysis_run
    )
    from .id_utils import generate_analysis_id, generate_result_id
except (ImportError, ValueError):
    from database import (
        execute_query,
        get_analysis_lineage,
        insert_analysis_input,
        insert_analysis_result,
        insert_analysis_run,
        update_analysis_run
    )
    from id_utils import generate_analysis_id, generate_result_id

ALGORITHM_NAME = "SolarAtlas_Engineering_Yield_Estimator"
ALGORITHM_VERSION = "v1.2.0"

# CEA India Baseline Carbon Emission Factor (CO2 Baseline Database for the Indian Power Sector)
INDIA_GRID_EMISSION_FACTOR_KG_PER_KWH = 0.716


class SolarAnalysisAssumptions:
    """Explicit parameters and engineering assumptions for solar calculations."""
    def __init__(
        self,
        roof_area_m2: float = 120.0,
        usable_area_percent: float = 70.0,
        kwp_per_10m2: float = 1.0,
        performance_ratio: float = 0.75,
        tariff_inr_per_kwh: float = 7.0,
        override_daily_ghi: Optional[float] = None
    ):
        if roof_area_m2 <= 0:
            raise ValueError("Rooftop area must be strictly positive.")
        if not (0 < usable_area_percent <= 100):
            raise ValueError("Usable rooftop percentage must be between 0 and 100.")
        if not (0.3 <= performance_ratio <= 0.95):
            raise ValueError("Performance ratio must be between 0.30 and 0.95 (typical 0.75).")

        self.roof_area_m2 = float(roof_area_m2)
        self.usable_area_percent = float(usable_area_percent)
        self.kwp_per_10m2 = float(kwp_per_10m2)
        self.performance_ratio = float(performance_ratio)
        self.tariff_inr_per_kwh = float(tariff_inr_per_kwh)
        self.override_daily_ghi = float(override_daily_ghi) if override_daily_ghi is not None else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roof_area_m2": self.roof_area_m2,
            "usable_area_percent": self.usable_area_percent,
            "kwp_per_10m2": self.kwp_per_10m2,
            "performance_ratio": self.performance_ratio,
            "tariff_inr_per_kwh": self.tariff_inr_per_kwh,
            "override_daily_ghi": self.override_daily_ghi,
            "grid_emission_factor_kg_per_kwh": INDIA_GRID_EMISSION_FACTOR_KG_PER_KWH,
            "formula_reference": "E_annual (kWh) = Capacity (kWp) * GHI (kWh/m2/day) * 365 * PR"
        }


def calculate_solar_potential(
    assumptions: SolarAnalysisAssumptions,
    daily_ghi_kwh_m2_day: float
) -> Dict[str, Any]:
    """
    Perform physics-grounded solar calculations.

    Formulas:
    1. Usable Area = Total Area * (Usable % / 100) [m²]
    2. Capacity = (Usable Area / 10) * kWp_per_10m² [kWp]
    3. Daily Energy = Capacity * Daily GHI * PR [kWh/day]
    4. Annual Energy = Daily Energy * 365 [kWh/year]
    5. Avoided CO2 = Annual Energy * 0.716 kg/kWh [kg CO2/year]
    6. Annual Savings = Annual Energy * Tariff [₹/year]
    """
    usable_area_m2 = round(assumptions.roof_area_m2 * (assumptions.usable_area_percent / 100.0), 2)
    capacity_kwp = round((usable_area_m2 / 10.0) * assumptions.kwp_per_10m2, 3)

    daily_yield_kwh = round(capacity_kwp * daily_ghi_kwh_m2_day * assumptions.performance_ratio, 2)
    annual_yield_kwh = round(daily_yield_kwh * 365.0, 1)

    avoided_co2_kg = round(annual_yield_kwh * INDIA_GRID_EMISSION_FACTOR_KG_PER_KWH, 1)
    annual_savings_inr = round(annual_yield_kwh * assumptions.tariff_inr_per_kwh, 2)

    return {
        "usable_area_m2": usable_area_m2,
        "capacity_kwp": capacity_kwp,
        "daily_ghi_kwh_m2_day": daily_ghi_kwh_m2_day,
        "daily_yield_kwh": daily_yield_kwh,
        "annual_yield_kwh": annual_yield_kwh,
        "avoided_co2_kg_year": avoided_co2_kg,
        "annual_savings_inr": annual_savings_inr
    }


def run_solar_analysis(
    input_datasets: List[Dict[str, Any]],
    assumptions: SolarAnalysisAssumptions,
    location_id: Optional[str] = None,
    output_dir: Optional[Union[str, Path]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Execute end-to-end solar analysis linking retrieved datasets to authoritative results in SQLite.
    Preserves audit lineage and physical units.
    """
    analysis_id = generate_analysis_id()

    # Determine location from datasets or fallback
    resolved_location_id = location_id
    if not resolved_location_id and input_datasets:
        resolved_location_id = input_datasets[0]["location"]["location_id"]

    if not resolved_location_id:
        # Fallback to first available location in SQLite
        locs = execute_query("SELECT location_id FROM locations LIMIT 1", db_path=db_path)
        resolved_location_id = locs[0]["location_id"] if locs else "loc_default"

    # Extract or resolve Global Horizontal Irradiance (GHI)
    daily_ghi = assumptions.override_daily_ghi
    is_demonstration = True

    if daily_ghi is None and input_datasets:
        # Look for mean GHI from observations or raster stats
        for ds in input_datasets:
            obs_sum = ds.get("observations_summary", {})
            if obs_sum.get("mean_ghi_kwh_m2_day"):
                daily_ghi = obs_sum["mean_ghi_kwh_m2_day"]
                is_demonstration = ds.get("is_demonstration", True)
                break

    if daily_ghi is None:
        daily_ghi = 5.05  # Standard demonstration assumption for northern India (Rampur/Delhi region)

    # Record analysis start in SQLite
    config = {
        "engine": "solar_atlas_backend",
        "inputs_count": len(input_datasets),
        "dataset_ids": [d["dataset_id"] for d in input_datasets]
    }
    insert_analysis_run(
        analysis_id=analysis_id,
        location_id=resolved_location_id,
        algorithm_name=ALGORITHM_NAME,
        algorithm_version=ALGORITHM_VERSION,
        configuration=config,
        assumptions=assumptions.to_dict(),
        status="running",
        db_path=db_path
    )

    try:
        # Record input lineage in analysis_inputs
        for ds in input_datasets:
            ds_id = ds["dataset_id"]
            asset_id = ds["assets"][0]["asset_id"] if ds.get("assets") else None
            role = "primary_irradiance" if "irradiance" in ds.get("data_type", "") else "auxiliary_gis"
            insert_analysis_input(
                analysis_id=analysis_id,
                dataset_id=ds_id,
                asset_id=asset_id,
                role=role,
                db_path=db_path
            )

        # Compute physics-grounded values
        results = calculate_solar_potential(assumptions, daily_ghi)

        # Store individual metrics in analysis_results
        metrics_to_store = [
            ("usable_roof_area", results["usable_area_m2"], "m2", 0.95),
            ("system_capacity", results["capacity_kwp"], "kWp", 0.90),
            ("daily_ghi_input", results["daily_ghi_kwh_m2_day"], "kWh/m2/day", 0.85),
            ("daily_energy_yield", results["daily_yield_kwh"], "kWh/day", 0.85),
            ("annual_energy_yield", results["annual_yield_kwh"], "kWh/year", 0.85),
            ("avoided_co2_emissions", results["avoided_co2_kg_year"], "kg_co2/year", 0.80),
            ("annual_financial_savings", results["annual_savings_inr"], "INR/year", 0.80),
        ]

        for metric_name, val, unit, conf in metrics_to_store:
            r_id = generate_result_id()
            insert_analysis_result(
                result_id=r_id,
                analysis_id=analysis_id,
                metric_name=metric_name,
                numerical_value=val,
                unit=unit,
                confidence_score=conf,
                metadata={"is_demonstration": is_demonstration},
                db_path=db_path
            )

        # Mark completed
        update_analysis_run(analysis_id, status="completed", db_path=db_path)

        lineage = get_analysis_lineage(analysis_id, db_path=db_path)
        return {
            "analysis_id": analysis_id,
            "status": "completed",
            "algorithm": f"{ALGORITHM_NAME} ({ALGORITHM_VERSION})",
            "is_demonstration": is_demonstration,
            "results": results,
            "lineage": lineage
        }

    except Exception as e:
        update_analysis_run(analysis_id, status="failed", error_message=str(e), db_path=db_path)
        raise RuntimeError(f"Solar analysis run {analysis_id} failed: {str(e)}") from e
