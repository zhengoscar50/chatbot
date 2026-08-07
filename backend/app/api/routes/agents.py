from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
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
)
from app.services.agent_service import AgentService, get_agent_service
from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def _trained(row: dict) -> bool:
    return bool(row.get("kb_id") or row.get("kb_full_id"))


def _permanent_kb_ids(row: dict) -> list:
    return [kb for kb in (row.get("kb_id"), row.get("kb_full_id")) if kb]


def _to_response(row: dict) -> AgentResponse:
    return AgentResponse(
        id=row["id"],
        name=row["name"],
        instructions=row.get("instructions") or "",
        model=row["model"],
        grounding=row.get("grounding", "strict"),
        use_general_kb=bool(row.get("use_general_kb")),
        trained=_trained(row),
    )


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    req: AgentCreateRequest,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    settings=Depends(get_settings),
):
    try:
        row = await run_in_threadpool(
            agents.create, user["id"], req.name, req.instructions,
            req.model or settings.default_agent_model, req.grounding, req.use_general_kb,
        )
    except PowabaseAPIError as e:
        # A bad model id is rejected here by the provider, not by a local list
        # that would rot as models come and go.
        raise HTTPException(status_code=400, detail=str(e))
    return _to_response(row)


@router.get("", response_model=list)
async def list_agents(
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
):
    rows = await run_in_threadpool(agents.list, user["id"])
    return [
        AgentSummary(
            id=r["id"], name=r["name"], model=r["model"],
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
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
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
    service = IngestService(
        client, None,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_max_wait_seconds,
    )

    def _run() -> dict:
        # Content-aware routing lives here, on the curated tier: a small document
        # stays whole, a large one is chunked.
        source_id = service.start(file.filename, content)
        service.await_extraction(source_id)
        full_document = 0 < service.char_count(source_id) <= settings.full_document_max_chars
        kb_id = agents.ensure_kb(row, full_document)
        return {"source_id": source_id, "status": service.index_into(kb_id, source_id)}

    try:
        result = await run_in_threadpool(_run)
    except AttentionRequiredError:
        raise HTTPException(
            status_code=422, detail=f"Could not read {file.filename}; it may need OCR."
        )
    except (ExtractionFailedError, IndexingFailedError) as e:
        raise HTTPException(status_code=422, detail=e.message)
    except IngestTimeoutError as e:
        raise HTTPException(status_code=504, detail=f"Still {e.status} after the maximum wait.")
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return IngestResponse(**result)


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
        out = []
        for kb_id in _permanent_kb_ids(row):
            for item in client.list_kb_sources(kb_id).get("items", []):
                out.append(AgentDocument(
                    source_id=item.get("source_id"),
                    filename=item.get("filename"),
                    status=item.get("status"),
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
        for kb_id in _permanent_kb_ids(row):
            items = client.list_kb_sources(kb_id).get("items", [])
            if any(item.get("source_id") == source_id for item in items):
                client.remove_source_from_kb(kb_id, source_id)
                return True
        return False

    try:
        found = await run_in_threadpool(_unlink)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="Document not found on this agent")
    return None
