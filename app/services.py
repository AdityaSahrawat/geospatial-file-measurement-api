import logging
import os
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from fastapi import UploadFile
from shapely.geometry import Polygon, MultiPolygon, LineString, MultiLineString
from sqlalchemy.orm import Session

from app.schema import FileRecord, FeatureRecord, SessionLocal

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


# ---------------------------------------------------------
# File processing
# ---------------------------------------------------------

async def process_uploaded_file(file: UploadFile):
    """
    Complete processing pipeline:

    Upload
        ↓
    Validate
        ↓
    Extract/read geospatial file
        ↓
    Extract features
        ↓
    Determine CRS
        ↓
    Transform geometry
        ↓
    Calculate measurements
        ↓
    Store in PostgreSQL
    """

    logger.info("[1/6] Reading file: %s", file.filename)
    contents = await file.read()
    logger.info("[1/6] File size: %d bytes", len(contents))

    if len(contents) > MAX_FILE_SIZE:
        raise ValueError("File size exceeds the 50 MB limit.")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:

            temp_path = Path(temp_dir) / file.filename

            with open(temp_path, "wb") as f:
                f.write(contents)

            logger.info("[2/6] Preparing source file from: %s", temp_path)
            source_path = _prepare_source_file(temp_path, Path(temp_dir))
            logger.info("[2/6] Source path resolved: %s", source_path)

            logger.info("[3/6] Reading geospatial file")
            gdf = _read_geospatial_file(source_path)
            logger.info("[3/6] Loaded %d features, CRS: %s", len(gdf), gdf.crs)

            if gdf.empty:
                raise ValueError("The uploaded file contains no features.")

            source_crs = gdf.crs

            if source_crs is None:
                raise ValueError("The input file does not contain a CRS.")

            logger.info("[4/6] Selecting projected CRS")
            projected_crs = _select_projected_crs(gdf)
            logger.info("[4/6] Projected CRS: %s", projected_crs)

            logger.info("[5/6] Reprojecting and calculating measurements")
            projected_gdf = gdf.to_crs(projected_crs)
            measurements = _calculate_measurements(gdf, projected_gdf)
            logger.info("[5/6] Computed %d measurements", len(measurements))

            # -------------------------------------------------
            # Persist to PostgreSQL
            # -------------------------------------------------

            logger.info("[6/6] Saving to PostgreSQL")
            with SessionLocal() as db:
                file_record = _save_file(
                    db=db,
                    filename=file.filename,
                    feature_count=len(gdf),
                    source_crs=source_crs.to_string(),
                    measurement_crs=projected_crs.to_string(),
                    status="completed",
                )
                logger.info("[6/6] FileRecord saved with id=%d", file_record.id)

                for i, measurement in enumerate(measurements):
                    logger.debug(
                        "[6/6] Saving feature %d/%d: index=%d type=%s area=%s length=%s",
                        i + 1, len(measurements),
                        measurement["feature_index"],
                        measurement["geometry_type"],
                        measurement["area_m2"],
                        measurement["length_m"],
                    )
                    _save_feature(
                        db=db,
                        file_id=file_record.id,
                        measurement=measurement,
                    )

            logger.info("[6/6] All features saved successfully")

            return {
                "id": file_record.id,
                "filename": file.filename,
                "feature_count": len(gdf),
                "crs": source_crs.to_string(),
                "measurement_crs": projected_crs.to_string(),
                "status": "completed",
            }

    except (ValueError, RuntimeError):
        raise  # let api.py handle known errors
    except Exception:
        logger.exception("Unexpected error processing file: %s", file.filename)
        raise


# ---------------------------------------------------------
# File extraction
# ---------------------------------------------------------

def _prepare_source_file(
    file_path: Path,
    extraction_dir: Path,
) -> Path:

    if file_path.suffix.lower() == ".kml":
        return file_path

    if file_path.suffix.lower() != ".zip":
        raise ValueError("Unsupported file format.")

    extract_dir = extraction_dir / "extracted"
    extract_dir.mkdir()

    _safe_extract_zip(file_path, extract_dir)

    shapefiles = list(extract_dir.rglob("*.shp"))

    if not shapefiles:
        raise ValueError(
            "ZIP file does not contain a Shapefile."
        )

    if len(shapefiles) > 1:
        raise ValueError(
            "ZIP file contains multiple Shapefiles."
        )

    return shapefiles[0]


def _safe_extract_zip(
    zip_path: Path,
    destination: Path,
):
    """
    Prevent path traversal attacks such as:

    ../../malicious_file
    """

    destination = destination.resolve()

    with zipfile.ZipFile(zip_path, "r") as archive:

        for member in archive.infolist():

            member_path = (
                destination / member.filename
            ).resolve()

            if not str(member_path).startswith(
                str(destination)
            ):
                raise ValueError(
                    "Unsafe ZIP file detected."
                )

        archive.extractall(destination)


# ---------------------------------------------------------
# Geospatial reading
# ---------------------------------------------------------

def _read_geospatial_file(path: Path) -> gpd.GeoDataFrame:

    try:
        gdf = gpd.read_file(path)
    except Exception as exc:
        raise ValueError(
            f"Unable to read geospatial file: {exc}"
        )

    if "geometry" not in gdf.columns:
        raise ValueError(
            "Geospatial file does not contain geometry."
        )

    return gdf


# ---------------------------------------------------------
# CRS selection
# ---------------------------------------------------------

def _select_projected_crs(
    gdf: gpd.GeoDataFrame,
):
    """
    Select a CRS suitable for planar measurements.

    Strategy:

    1. If the source CRS is already projected,
       use it directly.

    2. Otherwise estimate a suitable UTM CRS
       from the geometry extent.
    """

    if gdf.crs is None:
        raise ValueError("Input CRS is missing.")

    if gdf.crs.is_projected:
        return gdf.crs

    try:
        projected_crs = gdf.estimate_utm_crs()

        if projected_crs is None:
            raise ValueError(
                "Unable to determine a suitable projected CRS."
            )

        return projected_crs

    except Exception as exc:
        raise ValueError(
            f"Unable to select projected CRS: {exc}"
        )


# ---------------------------------------------------------
# Measurements
# ---------------------------------------------------------

def _calculate_measurements(
    original_gdf: gpd.GeoDataFrame,
    projected_gdf: gpd.GeoDataFrame,
):
    import math

    results = []

    for index, (original_row, projected_row) in enumerate(
        zip(
            original_gdf.itertuples(),
            projected_gdf.itertuples(),
        )
    ):

        original_geometry = original_row.geometry
        projected_geometry = projected_row.geometry

        # Skip features with null/missing geometry
        if original_geometry is None or projected_geometry is None:
            continue

        geometry_type = original_geometry.geom_type

        result = {
            "feature_index": index,
            "geometry_type": geometry_type,
            "properties": _extract_properties(
                original_row
            ),
            "area_m2": None,
            "length_m": None,
        }

        # Polygon / MultiPolygon
        if isinstance(
            projected_geometry,
            (Polygon, MultiPolygon),
        ):
            area = projected_geometry.area
            result["area_m2"] = None if (area is None or math.isnan(area)) else area

        # LineString / MultiLineString
        elif isinstance(
            projected_geometry,
            (LineString, MultiLineString),
        ):
            length = projected_geometry.length
            result["length_m"] = None if (length is None or math.isnan(length)) else length

        # Point and unsupported geometry:
        # leave measurements as None.

        results.append(result)

    return results


def _extract_properties(row):
    """
    Convert GeoPandas row into JSON-compatible
    properties, excluding geometry.

    - NaN  → None  (float null)
    - NaT  → None  (datetime null — common in KML files)
    - datetime → ISO string
    """

    import math
    import pandas as pd
    from datetime import datetime

    properties = {}

    for field in row._fields:

        if field == "geometry":
            continue

        value = getattr(row, field)

        # pandas NaT ("Not a Time") — datetime equivalent of NaN
        if value is pd.NaT:
            properties[field] = None
            continue

        # Convert NumPy scalar values where required.
        if hasattr(value, "item"):
            try:
                value = value.item()
            except (ValueError, TypeError):
                pass

        # NaN float → None
        try:
            if value is not None and math.isnan(value):
                value = None
        except (TypeError, ValueError):
            pass  # non-numeric types: ignore

        # datetime → ISO 8601 string so JSON can serialize it
        if isinstance(value, datetime):
            value = value.isoformat()

        properties[field] = value

    return properties


# ---------------------------------------------------------
# PostgreSQL persistence
# ---------------------------------------------------------

def _save_file(
    db: Session,
    filename: str,
    feature_count: int,
    source_crs: str,
    measurement_crs: str,
    status: str,
) -> FileRecord:
    """Insert a FileRecord row and return it with its generated id."""

    record = FileRecord(
        filename=filename,
        feature_count=feature_count,
        crs=source_crs,
        measurement_crs=measurement_crs,
        status=status,
    )

    db.add(record)
    db.commit()
    db.refresh(record)

    return record


def _save_feature(
    db: Session,
    file_id: int,
    measurement: dict,
) -> FeatureRecord:
    """Insert a FeatureRecord row for a single geometry feature."""

    record = FeatureRecord(
        file_id=file_id,
        feature_index=measurement["feature_index"],
        geometry_type=measurement["geometry_type"],
        properties=measurement["properties"],
        area_m2=measurement["area_m2"],
        length_m=measurement["length_m"],
    )

    db.add(record)
    db.commit()

    return record


# ---------------------------------------------------------
# GET operations
# ---------------------------------------------------------

def get_file_info(file_id: int):
    """Return metadata for a single file record, or None if not found."""

    with SessionLocal() as db:
        record = (
            db.query(FileRecord)
            .filter(FileRecord.id == file_id)
            .first()
        )

    if record is None:
        return None

    return {
        "id": record.id,
        "filename": record.filename,
        "feature_count": record.feature_count,
        "crs": record.crs,
        "measurement_crs": record.measurement_crs,
        "status": record.status,
    }


def get_file_measurements(file_id: int):
    """Return all feature measurements for a file, or None if file not found."""

    with SessionLocal() as db:
        record = (
            db.query(FileRecord)
            .filter(FileRecord.id == file_id)
            .first()
        )

        if record is None:
            return None

        features = (
            db.query(FeatureRecord)
            .filter(FeatureRecord.file_id == file_id)
            .order_by(FeatureRecord.feature_index)
            .all()
        )

    return {
        "file_id": file_id,
        "measurements": [
            {
                "feature_index": feature.feature_index,
                "geometry_type": feature.geometry_type,
                "area_m2": feature.area_m2,
                "length_m": feature.length_m,
            }
            for feature in features
        ],
    }