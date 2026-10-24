"""
id_utils.py - Unique Data-Address and Identifier System
Solar Atlas: Space Hackathon AI for Solar Energy Potential Mapping

Provides collision-resistant UUID-based identifiers, SHA-256 checksum calculations,
storage URI resolution, and idempotency key generation.
Separates: Unique ID, Storage Address (URI), Checksum (SHA-256), and Metadata.
"""

from __future__ import annotations
import hashlib
from pathlib import Path
import uuid
from typing import Union


def generate_source_id() -> str:
    """Generate a unique ID for a data provider/source (e.g., src_bhuvan_...)."""
    return f"src_{uuid.uuid4().hex[:12]}"


def generate_location_id() -> str:
    """Generate a unique ID for a geographic study area."""
    return f"loc_{uuid.uuid4().hex[:12]}"


def generate_dataset_id() -> str:
    """Generate a unique ID for a dataset version."""
    return f"ds_{uuid.uuid4().hex[:12]}"


def generate_asset_id() -> str:
    """Generate a unique ID for an individual file/raster/vector asset."""
    return f"ast_{uuid.uuid4().hex[:12]}"


def generate_observation_id() -> str:
    """Generate a unique ID for a structured solar observation record."""
    return f"obs_{uuid.uuid4().hex[:12]}"


def generate_vector_record_id() -> str:
    """Generate a unique ID for a vector database document chunk."""
    return f"vec_{uuid.uuid4().hex[:12]}"


def generate_pipeline_run_id() -> str:
    """Generate a unique ID for a pipeline execution."""
    return f"run_{uuid.uuid4().hex[:12]}"


def generate_analysis_id() -> str:
    """Generate a unique ID for a solar analysis execution."""
    return f"anl_{uuid.uuid4().hex[:12]}"


def generate_result_id() -> str:
    """Generate a unique ID for an individual analysis result metric."""
    return f"res_{uuid.uuid4().hex[:12]}"


def calculate_sha256(file_path: Union[str, Path]) -> str:
    """
    Compute cryptographic SHA-256 checksum of a file.
    Reads in 64KB chunks to efficiently handle large satellite rasters.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Cannot calculate checksum. File not found: {file_path}")

    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def calculate_content_hash(text: str) -> str:
    """Compute SHA-256 hash of a string chunk for vector record deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_idempotency_key(
    file_checksum: str,
    location_name: str,
    data_type: str,
    version: str = "v1.0"
) -> str:
    """
    Generate an idempotency key combining file content hash and business coordinates.
    Ensures repeated ingestion requests return the existing dataset rather than creating duplicates.
    """
    raw_key = f"{file_checksum}:{location_name.strip().lower()}:{data_type.strip().lower()}:{version.strip()}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def path_to_uri(file_path: Union[str, Path]) -> str:
    """Convert local Windows/POSIX path to normalized file:// URI or relative POSIX storage URI."""
    p = Path(file_path).resolve()
    return p.as_uri()
