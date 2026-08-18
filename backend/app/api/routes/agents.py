import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.models.schemas import (
    AgentCreateRequest,
    AgentDocument,
    AgentResponse,
    AgentSummary,
    AgentUpdateRequest,
    IngestResponse,
    IngestStatusResponse,
)
from app.services.agent_service import AgentService, ModelRejectedError, get_agent_service
from app.services.chatbot_service import ChatbotService, get_chatbot_service
from app.services.context_budget import clamp_context_tokens, max_context_for
from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
    source_status,
)

router = APIRouter(prefix="/agents", tags=["agents"])
logger = logging.getLogger(__name__)


def _finish_training(service, agents, row, source_id, full_document_max_chars) -> None:
    """Extract, classify and index one training document, after the response.

    Content-aware routing lives here, on the curated tier: a small document
    stays whole, a large one is chunked.

    Failures are logged, not swallowed. The equivalent chat-upload task passes
    silently, which means a document can fail to arrive with nothing anywhere
    saying why; progress is visible through the status endpoint, but a cause
    is not.
    """
    try:
        service.await_extraction(source_id)
        full_document = 0 < service.char_count(source_id) <= full_document_max_chars
        kb_id = agents.ensure_kb(row, full_document)
        service.index_into(kb_id, source_id)
    except AttentionRequiredError:
        logger.warning("training %s: needs OCR re-extraction", source_id)
    except (ExtractionFailedError, IndexingFailedError) as e:
        logger.warning("training %s failed: %s", source_id, e.message)
    except IngestTimeoutError as e:
        logger.warning("training %s timed out while %s", source_id, e.status)
    except PowabaseAPIError as e:
        logger.warning("training %s: upstream %s", source_id, e.status_code)


def _trained(row: dict) -> bool:
    return bool(row.get("kb_id") or row.get("kb_full_id"))


def _permanent_kb_ids(row: dict) -> list:
    return [kb for kb in (row.get("kb_id"), row.get("kb_full_id")) if kb]


def _to_response(row: dict) -> AgentResponse:
    return AgentResponse(
        id=row["id"],
        name=row["name"],
        instructions=row.get("instructions") or "",
        description=row.get("description") or "",
        model=row["model"],
        grounding=row.get("grounding", "strict"),
        use_general_kb=bool(row.get("use_general_kb")),
        trained=_trained(row),
        max_context_tokens=clamp_context_tokens(
            row.get("max_context_tokens"), row.get("model")
        ),
        max_context_ceiling=max_context_for(row.get("model")),
    )


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    req: AgentCreateRequest,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    settings=Depends(get_settings),
):
    if await run_in_threadpool(chatbots.get_owned, req.chatbot_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        row = await run_in_threadpool(
            agents.create, req.chatbot_id, user["id"], req.name, req.instructions, req.description,
            req.model or settings.default_agent_model, req.grounding, req.use_general_kb,
            req.max_context_tokens,
        )
    except ModelRejectedError as e:
        raise HTTPException(
            status_code=400,
            detail=f"The model '{e.model}' isn't available. Check the id, or leave it blank to use the default.",
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_response(row)


@router.get("", response_model=list)
async def list_agents(
    chatbot_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    if await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        rows = await run_in_threadpool(agents.list, chatbot_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return [
        AgentSummary(
            id=r["id"], name=r["name"], model=r["model"],
            description=r.get("description") or "",
            trained=_trained(r), updated_at=r.get("updated_at"),
        )
        for r in rows
    ]


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
):
    try:
        row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _to_response(row)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    req: AgentUpdateRequest,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    fields = req.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        return _to_response(row)
    try:
        merged = await run_in_threadpool(agents.update, row, fields)
    except ModelRejectedError as e:
        raise HTTPException(
            status_code=400,
            detail=f"The model '{e.model}' isn't available. The agent is unchanged.",
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_response(merged)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        await run_in_threadpool(agents.delete, agent_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return None


@router.post("/{agent_id}/train", response_model=IngestResponse)
async def train_agent(
    background_tasks: BackgroundTasks,
    agent_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    content = await file.read()
    # Backgrounded, with the long budget, exactly like a chat upload. Training
    # used to block the request and wait only `ingest_max_wait_seconds` (60s),
    # so a large document could not be trained at all: a 350k-character PDF
    # takes minutes to extract, and the request 504'd — or died on a transient
    # upstream blip — long before it finished. Progress is reported by
    # GET /agents/{id}/documents/{source_id}/status.
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
        _finish_training, service, agents, row, source_id,
        settings.full_document_max_chars,
    )
    return JSONResponse(
        status_code=202,
        content=IngestResponse(source_id=source_id, status="processing").model_dump(),
    )


@router.get("/{agent_id}/documents/{source_id}/status", response_model=IngestStatusResponse)
async def agent_document_status(
    agent_id: str,
    source_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    kb_ids = _permanent_kb_ids(row)
    try:
        status, detail = await run_in_threadpool(source_status, client, source_id, kb_ids)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return IngestStatusResponse(source_id=source_id, status=status, detail=detail)


@router.get("/{agent_id}/documents", response_model=list)
async def list_agent_documents(
    agent_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    def _collect() -> list:
        # Powabase names these source_name / index_status, not filename / status.
        out = []
        for kb_id in _permanent_kb_ids(row):
            for item in client.list_kb_sources(kb_id).get("items", []):
                out.append(AgentDocument(
                    source_id=item.get("source_id"),
                    filename=item.get("source_name"),
                    status=item.get("index_status"),
                ))
        return out

    try:
        return await run_in_threadpool(_collect)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/{agent_id}/documents/{source_id}", status_code=204)
async def untrain_agent_document(
    agent_id: str,
    source_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    def _unlink() -> bool:
        # Unlink from whichever permanent KB holds it. Never delete the Source:
        # upload_source reuses duplicates, so it may belong to other agents too.
        #
        # The unlink takes the *indexed-source* id (item["id"]), not the
        # source_id the caller passes us — those are different ids, and sending
        # the wrong one 404s with "Indexed source not found".
        for kb_id in _permanent_kb_ids(row):
            for item in client.list_kb_sources(kb_id).get("items", []):
                if item.get("source_id") == source_id:
                    client.remove_source_from_kb(kb_id, item["id"])
                    return True
        return False

    try:
        found = await run_in_threadpool(_unlink)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="Document not found on this agent")
    return None
