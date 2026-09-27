from fastapi import APIRouter, UploadFile, File, HTTPException, status

from app.services import (
    process_uploaded_file,
    get_file_info,
    get_file_measurements,
)

router = APIRouter()


@router.post(
    "/files/",
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(file: UploadFile = File(...)):
    """
    Upload a KML file or a ZIP containing a Shapefile.
    """

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Filename is required.",
        )

    filename = file.filename.lower()

    if not (filename.endswith(".kml") or filename.endswith(".zip")):
        raise HTTPException(
            status_code=400,
            detail="Only .kml and .zip files are supported.",
        )

    try:
        return await process_uploaded_file(file)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Failed to process geospatial file.",
        )


@router.get("/files/{file_id}")
def get_file(file_id: int):
    """
    Return basic information about a processed file.
    """

    result = get_file_info(file_id)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="File not found.",
        )

    return result


@router.get("/files/{file_id}/measurements/")
def get_measurements(file_id: int):
    """
    Return measurements for all features in a processed file.
    """

    result = get_file_measurements(file_id)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="File not found.",
        )

    return result