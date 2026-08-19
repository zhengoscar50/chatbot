"""A chatbot's knowledge base.

Trained once, searched by every agent in that chatbot — the general assistant
included. Distinct from /agents/{id}/train, which teaches one agent, and from a
chat's uploads, which are temporary until promoted.
"""
import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
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
from app.services.chatbot_service import ChatbotService, get_chatbot_service

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
logger = logging.getLogger(__name__)


def _finish_training(service, chatbot_kb, chatbot_row, source_id, full_document_max_chars) -> None:
    """Extract, classify and index one document, after the response.

    Same shape as agent training: backgrounded with the long budget, because a
    large PDF takes minutes to extract and a blocking request cannot wait that
    long. Failures are logged rather than swallowed — progress is visible
    through the status endpoint, but a cause is not.
    """
    try:
        service.await_extraction(source_id)
        full_document = 0 < service.char_count(source_id) <= full_document_max_chars
        kb_id = chatbot_kb.ensure_kb(chatbot_row, full_document)
        service.index_into(kb_id, source_id)
    except AttentionRequiredError:
        logger.warning("chatbot knowledge %s: needs OCR re-extraction", source_id)
    except (ExtractionFailedError, IndexingFailedError) as e:
        logger.warning("chatbot knowledge %s failed: %s", source_id, e.message)
    except IngestTimeoutError as e:
        logger.warning("chatbot knowledge %s timed out while %s", source_id, e.status)
    except PowabaseAPIError as e:
        logger.warning("chatbot knowledge %s: upstream %s", source_id, e.status_code)


@router.post("/train", response_model=IngestResponse)
async def train_chatbot_knowledge(
    background_tasks: BackgroundTasks,
    chatbot_id: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    chatbot = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
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
        _finish_training, service, chatbot_kb, chatbot, source_id,
        settings.full_document_max_chars,
    )
    return JSONResponse(
        status_code=202,
        content=IngestResponse(source_id=source_id, status="processing").model_dump(),
    )


@router.get("/documents/{source_id}/status", response_model=IngestStatusResponse)
async def chatbot_knowledge_status(
    source_id: str,
    chatbot_id: str = Query(...),
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    # get_owned re-reads the row, which is also what this needs: the tier is
    # created during the background task, so any earlier copy predates it.
    chatbot = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        status, detail = await run_in_threadpool(
            source_status, client, source_id, chatbot_kb.kb_ids(chatbot)
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return IngestStatusResponse(source_id=source_id, status=status, detail=detail)


@router.get("/documents", response_model=list)
async def list_chatbot_knowledge(
    chatbot_id: str = Query(...),
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    chatbot = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        return await run_in_threadpool(chatbot_kb.documents, chatbot)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/documents/{source_id}", status_code=204)
async def untrain_chatbot_knowledge(
    source_id: str,
    chatbot_id: str = Query(...),
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    chatbot = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        found = await run_in_threadpool(chatbot_kb.untrain, chatbot, source_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="Document not found")
    return None
