from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.models.schemas import IngestResponse, IngestStatusResponse
from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
    source_status,
)
from app.services.session_service import SessionService, get_session_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _run_finish(service: IngestService, sessions: SessionService, row: dict, source_id: str, max_chars: int) -> None:
    # Runs post-response: decide the KB by extracted size, then index.
    # Failures are observable via GET /ingest/status.
    try:
        service.await_extraction(source_id)
        full_document = 0 < service.char_count(source_id) <= max_chars
        kb_id = sessions.ensure_kb(row, full_document)
        service.index_into(kb_id, source_id)
    except (
        AttentionRequiredError,
        ExtractionFailedError,
        IndexingFailedError,
        IngestTimeoutError,
        PowabaseAPIError,
    ):
        pass


@router.post("/file", response_model=IngestResponse)
async def ingest_file(
    background_tasks: BackgroundTasks,
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

    service = IngestService(
        client,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_background_max_wait_seconds,
    )
    try:
        source_id = await run_in_threadpool(service.start, file.filename, content)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    background_tasks.add_task(
        _run_finish, service, sessions, row, source_id, settings.full_document_max_chars
    )
    return JSONResponse(
        status_code=202,
        content=IngestResponse(source_id=source_id, status="processing").model_dump(),
    )


@router.get("/status/{source_id}", response_model=IngestStatusResponse)
async def ingest_status(
    source_id: str,
    session_id: str,
    user: dict = Depends(get_current_user),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
):
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    kb_ids = [row.get("kb_id"), row.get("kb_full_id")]
    try:
        status, detail = await run_in_threadpool(source_status, client, source_id, kb_ids)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return IngestStatusResponse(source_id=source_id, status=status, detail=detail)
