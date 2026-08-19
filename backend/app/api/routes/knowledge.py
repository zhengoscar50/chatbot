"""The signed-in user's personal knowledge base.

Trained once, searched by every agent that user owns. Distinct from
/admin/train, which curates the shared general KB for everyone, and from
/agents/{id}/train, which teaches one agent.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
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
from app.services.chatbot_kb import ChatbotKbService, get_chatbot_kb_service

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
logger = logging.getLogger(__name__)


def _finish_training(service, chatbot_kb, user_row, source_id, full_document_max_chars) -> None:
    """Extract, classify and index one document, after the response.

    Same shape as agent training: backgrounded with the long budget, because a
    large PDF takes minutes to extract and a blocking request cannot wait that
    long. Failures are logged rather than swallowed — progress is visible
    through the status endpoint, but a cause is not.
    """
    try:
        service.await_extraction(source_id)
        full_document = 0 < service.char_count(source_id) <= full_document_max_chars
        kb_id = chatbot_kb.ensure_kb(user_row, full_document)
        service.index_into(kb_id, source_id)
    except AttentionRequiredError:
        logger.warning("user knowledge %s: needs OCR re-extraction", source_id)
    except (ExtractionFailedError, IndexingFailedError) as e:
        logger.warning("user knowledge %s failed: %s", source_id, e.message)
    except IngestTimeoutError as e:
        logger.warning("user knowledge %s timed out while %s", source_id, e.status)
    except PowabaseAPIError as e:
        logger.warning("user knowledge %s: upstream %s", source_id, e.status_code)


@router.post("/train", response_model=IngestResponse)
async def train_user_knowledge(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    content = await file.read()
    service = IngestService(
        client, None,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_background_max_wait_seconds,
    )
    try:
        source_id = await run_in_threadpool(service.start, file.filename, content)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    background_tasks.add_task(
        _finish_training, service, chatbot_kb, user, source_id,
        settings.full_document_max_chars,
    )
    return JSONResponse(
        status_code=202,
        content=IngestResponse(source_id=source_id, status="processing").model_dump(),
    )


@router.get("/documents/{source_id}/status", response_model=IngestStatusResponse)
async def user_knowledge_status(
    source_id: str,
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    # Re-read the row: the tier is created during the background task, so the
    # token's copy of the user predates it.
    fresh = await run_in_threadpool(client.get_user, user["id"]) or user
    try:
        status, detail = await run_in_threadpool(
            source_status, client, source_id, chatbot_kb.kb_ids(fresh)
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return IngestStatusResponse(source_id=source_id, status=status, detail=detail)


@router.get("/documents", response_model=list)
async def list_user_knowledge(
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    fresh = await run_in_threadpool(client.get_user, user["id"]) or user
    try:
        return await run_in_threadpool(chatbot_kb.documents, fresh)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/documents/{source_id}", status_code=204)
async def untrain_user_knowledge(
    source_id: str,
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    fresh = await run_in_threadpool(client.get_user, user["id"]) or user
    try:
        found = await run_in_threadpool(chatbot_kb.untrain, fresh, source_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="Document not found")
    return None
