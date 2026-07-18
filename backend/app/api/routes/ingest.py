from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient
from app.core.config import get_settings
from app.models.schemas import IngestResponse
from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
)

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/file", response_model=IngestResponse)
async def ingest_file(file: UploadFile = File(...)):
    content = await file.read()
    settings = get_settings()
    client = PowabaseClient(settings.powabase_base_url, settings.powabase_service_role_key)
    service = IngestService(
        client,
        settings.powabase_kb_id,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_max_wait_seconds,
    )
    try:
        result = service.ingest_pdf(file.filename, content)
        return IngestResponse(**result)
    except AttentionRequiredError as e:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Source {e.source_id} needs OCR re-extraction (low-quality/scanned PDF). "
                f"Call POST /api/sources/{e.source_id}/reextract with an OCR extraction_model."
            ),
        )
    except (ExtractionFailedError, IndexingFailedError) as e:
        raise HTTPException(status_code=500, detail=e.message)
    except IngestTimeoutError as e:
        return JSONResponse(
            status_code=202,
            content=IngestResponse(source_id=e.source_id, status=e.status).model_dump(),
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
