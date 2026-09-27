# Geospatial File Measurement API

## Project Overview

A REST API that accepts geospatial files (KML or Shapefile ZIP), reads the geometries inside, calculates measurements (area, length) in real-world metres, and stores the results in PostgreSQL.

Supports:
- `.kml` — Keyhole Markup Language
- `.zip` — ZIP archive containing a Shapefile (`.shp` + sidecar files)

---

## How to Run

### With Docker Compose (recommended)

Requires: Docker Desktop

```bash
# Start PostgreSQL + API together
docker compose up --build

# API will be available at:
# http://localhost:8000
# http://localhost:8000/docs  ← interactive Swagger UI
```

PostgreSQL is exposed on host port `4444`.

### Without Docker (local dev)

Requires: Python 3.13+, `uv`, a running PostgreSQL instance

```bash
# 1. Install dependencies
uv sync

# 2. Set your database URL
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:4444/geospatial"

# 3. Start the server
uv run uvicorn app.main:app --reload

# API: http://localhost:8000
# Docs: http://localhost:8000/docs
```

---

## API Documentation

### `POST /api/files/`
Upload a geospatial file for processing.

- **Body**: `multipart/form-data`, field name `file`
- **Accepted**: `.kml`, `.zip` (containing a single `.shp`)
- **Returns**: file metadata + assigned `id`

```bash
curl -X POST http://localhost:8000/api/files/ \
  -F "file=@/path/to/your/file.kml"
```

**Response (201)**
```json
{
  "id": 1,
  "filename": "sample.kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "measurement_crs": "EPSG:32644",
  "status": "completed"
}
```

---

### `GET /api/files/{file_id}`
Get metadata for a previously uploaded file.

```bash
curl http://localhost:8000/api/files/1
```

---

### `GET /api/files/{file_id}/measurements/`
Get per-feature measurements for an uploaded file.

```bash
curl http://localhost:8000/api/files/1/measurements/
```

**Response**
```json
{
  "file_id": 1,
  "measurements": [
    {
      "feature_index": 0,
      "geometry_type": "Polygon",
      "area_m2": 9271695.55,
      "length_m": null
    },
    {
      "feature_index": 1,
      "geometry_type": "LineString",
      "area_m2": null,
      "length_m": 4823.12
    }
  ]
}
```

---

### `GET /health`
Health check.

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## Architecture

### Application Structure

```
app/
├── main.py       — FastAPI app, lifespan, router mounting
├── api.py        — Route definitions (APIRouter)
├── services.py   — Business logic: file processing, measurements, DB writes
└── schema.py     — SQLAlchemy ORM models, DB engine, session factory
```

### File-Processing Flow

```
HTTP POST /api/files/
    │
    ▼
Validate filename & extension (.kml / .zip)
    │
    ▼
Read file bytes → check size (max 50 MB)
    │
    ▼
Save to temp directory
    │
    ▼
KML? → use directly
ZIP? → safe-extract → find single .shp inside
    │
    ▼
GeoPandas reads the file into a GeoDataFrame
    │
    ▼
Validate: non-empty, CRS present
    │
    ▼
Select projected CRS (see CRS Handling below)
    │
    ▼
Reproject GeoDataFrame → calculate measurements
    │
    ▼
Save FileRecord + FeatureRecords to PostgreSQL
    │
    ▼
Return JSON response
```

### Measurement Calculation Flow

For each feature in the GeoDataFrame:

1. Skip features with `null` geometry
2. Detect geometry type (`Polygon`, `MultiPolygon`, `LineString`, etc.)
3. Use the **projected** geometry (in metres) to compute:
   - `area_m2` — for Polygon / MultiPolygon
   - `length_m` — for LineString / MultiLineString
   - Both `null` — for Point and other types
4. Sanitise `NaN` values → `None` (JSON `null`) before storing

### CRS Handling

A **CRS (Coordinate Reference System)** defines how coordinates map to real locations on Earth.

- **Geographic CRS** (e.g. `EPSG:4326`) — coordinates in degrees (lat/lon). Cannot measure area/length accurately in metres directly.
- **Projected CRS** (e.g. UTM zones) — coordinates in metres on a flat plane. Accurate for local measurements.

**Strategy:**
1. If the uploaded file already uses a projected CRS → use it as-is.
2. If it uses a geographic CRS (most common) → call `estimate_utm_crs()` to automatically pick the best UTM zone based on where the data is located.
3. Reproject the GeoDataFrame into that CRS → measure in metres.

---

## Design Decisions

### Dynamic UTM CRS selection
Uses `estimate_utm_crs()` to automatically pick a projected CRS based on where the uploaded data sits on the globe, instead of hardcoding a single CRS. This works correctly for most real-world datasets without any user input.

### Projected measurements
Geometries are reprojected into the selected projected CRS before calculating area or length. Measuring directly in lat/lon degrees gives meaningless results — degrees are not a unit of distance.

### Dataset-level CRS
One projected CRS is selected for the entire dataset and applied to all features. This keeps the pipeline simple and efficient. The trade-off is reduced accuracy for datasets that span multiple UTM zones (e.g. a global shapefile), where each feature ideally needs its own local CRS.

---

## Future Scope

- **Per-feature CRS selection** — pick an optimal projected CRS independently for each geometry to improve accuracy across datasets covering multiple UTM zones.
- **Large/multi-zone datasets** — add smarter handling for geometries that span large geographic regions or cross UTM zone boundaries.
- **Additional formats and measurements** — support GeoJSON, GeoPackage, and add measurements like centroid, bounding box, and perimeter.

---

## Learnings

- **Shapefiles** are made of multiple files (`.shp`, `.dbf`, `.prj`, etc.) that work together to store geometry, attributes, and CRS information. You need all of them to read the data correctly.
- **Geographic vs projected CRS** — geographic CRS uses degrees (lat/lon), which you cannot use to measure area or distance. You need a projected CRS (metres) to get real-world measurements.
- **GeoPandas, Shapely, PyProj** — GeoPandas reads and manages spatial data, Shapely does the geometry math (area, length), and PyProj handles CRS transformations between coordinate systems.
- **Backend pipeline design** — learned how to structure a multi-step pipeline: validate → extract → read → transform → measure → store, with proper error handling at each stage.
- **CRS accuracy trade-offs** — a single UTM zone is accurate locally but loses accuracy at global scale. Picking the right CRS for the data matters a lot for measurement quality.