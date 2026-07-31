from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.models.schemas import IngestResponse
from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
)
from app.services.session_service import SessionService, get_session_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/file", response_model=IngestResponse)
async def ingest_file(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
):
    content = await file.read()
    settings = get_settings()
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # The session's KB is created lazily — this first upload provisions it.
    kb_id = await run_in_threadpool(sessions.ensure_kb, row)
    service = IngestService(
        client,
        kb_id,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_max_wait_seconds,
    )
    try:
        result = await run_in_threadpool(service.ingest_pdf, file.filename, content)
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
