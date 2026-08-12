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
from app.services.scratch_kb import get_scratch_kb_id
from app.services.session_service import SessionService, get_session_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _run_finish(service: IngestService, sessions: SessionService, row: dict,
                source_id: str, scratch_kb_id: str) -> None:
    # Runs post-response: index into the shared scratch KB and record the
    # source on this chat, which is what scopes retrieval back to it.
    # Failures are observable via GET /ingest/status.
    #
    # Order matters: index first, record second. A source recorded but not
    # indexed would be sent to retrieval as a source_id the KB does not hold;
    # indexed but not recorded is merely invisible, and the upload can be
    # retried. Fail towards invisible.
    #
    # No content-aware routing here. Scratch uploads are throwaway context for a
    # single conversation, so the chunk/full split is reserved for an agent's
    # permanent tier (see POST /agents/{id}/train).
    try:
        service.await_extraction(source_id)
        service.index_into(scratch_kb_id, source_id)
        sessions.record_source(row["id"], source_id)
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
    scratch_kb_id: str = Depends(get_scratch_kb_id),
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
        _run_finish, service, sessions, row, source_id, scratch_kb_id
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
    scratch_kb_id: str = Depends(get_scratch_kb_id),
):
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # The shared scratch KB is where new uploads land; row["kb_id"] is the
    # legacy per-chat KB, still checked so older chats report status correctly.
    kb_ids = [scratch_kb_id, row.get("kb_id"), row.get("kb_full_id")]
    try:
        status, detail = await run_in_threadpool(source_status, client, source_id, kb_ids)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Indexed in the shared KB is not yet answerable HERE. The upload is
    # indexed first and recorded on the chat second, and only the recording
    # puts it in retrieval scope, so "indexed" in between would tell the UI a
    # document is ready while it still answers "I don't know" — and if the
    # recording never happens, that lie is permanent rather than momentary.
    # Legacy chats keep their documents in their own KB and have no
    # source_ids, so they are exempt rather than stuck on "processing".
    if status == "indexed" and not row.get("kb_id"):
        if source_id not in (row.get("source_ids") or []):
            status, detail = "processing", None

    return IngestStatusResponse(source_id=source_id, status=status, detail=detail)
