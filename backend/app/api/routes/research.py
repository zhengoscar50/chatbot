import time
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.models.schemas import ResearchRequest, ResearchStartResponse, ResearchStatusResponse
from app.services.general_kb import get_general_kb_id
from app.services.research_pipeline import get_research_orchestration_id
from app.services.research_service import STAGES, build_message, run_research
from app.services.session_service import SessionService, get_session_service

router = APIRouter(prefix="/research", tags=["research"])


@router.post("", response_model=ResearchStartResponse, status_code=202)
def start_research(
    req: ResearchRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
    general_kb_id: str = Depends(get_general_kb_id),
    orchestration_id: str = Depends(get_research_orchestration_id),
    settings=Depends(get_settings),
):
    row = sessions.get_owned_session(req.session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    jobs = request.app.state.research_jobs
    _evict_expired(jobs, settings.research_job_ttl_seconds)
    running = sum(1 for j in jobs.values()
                  if j.get("owner") == user["id"] and j["status"] == "running")
    if running >= settings.research_max_concurrent_per_user:
        raise HTTPException(
            status_code=429,
            detail=f"You already have {running} research runs in progress. Wait for one to finish.",
        )

    kbs = [
        {"id": kb, "top_k": settings.research_top_k}
        for kb in [row.get("kb_id"), row.get("kb_full_id"), general_kb_id] if kb
    ]
    try:
        handler = client.create_context_handler(req.query, kbs, settings.research_max_context_tokens)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    evidence = handler.get("formatted_context", "")
    citations = _citations_from(handler)

    job_id = str(uuid.uuid4())
    job = {"status": "running", "stage": STAGES[0], "report": None,
           "citations": citations, "detail": None, "owner": user["id"],
           "created_at": time.monotonic()}
    jobs[job_id] = job

    message = build_message(req.query, evidence)
    background_tasks.add_task(run_research, client, orchestration_id, job, message)
    return ResearchStartResponse(job_id=job_id, status="running")


@router.get("/status/{job_id}", response_model=ResearchStatusResponse)
def research_status(job_id: str, request: Request, user: dict = Depends(get_current_user)):
    job = request.app.state.research_jobs.get(job_id)
    if job is None or job.get("owner") != user["id"]:
        raise HTTPException(status_code=404, detail="Research job not found")
    return ResearchStatusResponse(
        status=job["status"], stage=job.get("stage"), report=job.get("report"),
        citations=job.get("citations", []), detail=job.get("detail"),
    )


def _evict_expired(jobs: dict, ttl_seconds: int) -> None:
    """Drop jobs older than the TTL so the in-memory store stays bounded.

    Age, not status, is the criterion: the TTL is far longer than the 5-minute
    window the frontend polls for, so a finished report is always collectable
    before it expires. Sweeping "running" jobs too means a run that never
    reaches a terminal event (a stream held open by keepalives) cannot occupy
    a per-user concurrency slot forever. Its worker keeps its own reference to
    the job dict, so evicting the entry is safe — the writes simply land on an
    object nothing reads anymore.
    """
    cutoff = time.monotonic() - ttl_seconds
    for job_id in [k for k, j in jobs.items() if j.get("created_at", 0) < cutoff]:
        del jobs[job_id]


def _citations_from(handler: dict) -> list:
    out = []
    for item in handler.get("retrieved_context", []):
        cid = item.get("source_name") or item.get("source_id")
        if cid and cid not in out:
            out.append(cid)
    return out
