import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.admin import router as admin_router
from app.api.routes.agents import router as agents_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.api.routes.ingest import router as ingest_router
from app.api.routes.models import router as models_router
from app.api.routes.sessions import router as sessions_router
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient
from app.core.config import FRONTEND_DIR, get_settings
from app.services.agent_service import AgentService
from app.services.general_assistant import ensure_general_assistant
from app.services.general_kb import ensure_general_kb
from app.services.orchestrator import ensure_orchestrator_agent
from app.services.retrieval import reranker_retrieval_config
from app.services.scratch_kb import ensure_scratch_kb
from app.services.session_service import SessionService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    client = PowabaseClient(settings.powabase_base_url, settings.powabase_service_role_key)
    try:
        try:
            client.list_agents()
            reranker_config = reranker_retrieval_config(
                settings.reranker_model, settings.reranker_candidate_count
            )
            general_kb_id = ensure_general_kb(client, reranker_config)
            scratch_kb_id = ensure_scratch_kb(client, reranker_config)
            orchestrator_agent_id = ensure_orchestrator_agent(
                client, settings.orchestrator_model
            )
            general_assistant_id = ensure_general_assistant(
                client, settings.general_assistant_model
            )
        except PowabaseAPIError as e:
            raise RuntimeError(f"Powabase is not reachable: {e}") from e
        app.state.powabase_client = client
        app.state.general_kb_id = general_kb_id
        app.state.scratch_kb_id = scratch_kb_id
        app.state.orchestrator_agent_id = orchestrator_agent_id
        app.state.general_assistant_id = general_assistant_id
        agent_service = AgentService(client, reranker_config)
        # Push the current grounding clause onto agents that already exist.
        # Prompts are otherwise only patched when a user edits an agent, so a
        # change to the shared clause would reach nothing already created.
        try:
            resynced = agent_service.resync_prompts()
            logger.info("re-synced system prompts on %d agent(s)", resynced)
        except PowabaseAPIError as e:
            logger.warning("prompt re-sync skipped: upstream %s", e.status_code)
        app.state.agent_service = agent_service
        app.state.session_service = SessionService(
            client, reranker_config, scratch_kb_id
        )
        yield
    finally:
        client.close()


logger = logging.getLogger(__name__)

# Upstream statuses worth retrying. Everything else means Powabase understood
# the request and refused it, so a retry sends the same request to the same
# answer.
RETRYABLE_UPSTREAM = frozenset({429, 500, 502, 503, 504})
RETRY_AFTER_SECONDS = "5"


def register_exception_handlers(app: FastAPI) -> None:
    """Turn an upstream failure into an honest status instead of a 500.

    Individual routes catch PowabaseAPIError where they have something useful
    to do with it. This is the net under everything else — most importantly
    `get_current_user`, which calls Powabase on every authenticated request and
    caught nothing. A single upstream blip there 500'd the entire API, login
    included, and logged a traceback pointing at this codebase for an outage
    somewhere else.
    """

    @app.exception_handler(PowabaseAPIError)
    async def powabase_upstream_failure(request: Request, exc: PowabaseAPIError):
        retryable = exc.status_code in RETRYABLE_UPSTREAM
        # Log the upstream detail; never return it. Bodies here are whatever the
        # gateway emitted — HTML, hostnames, internal messages.
        logger.warning(
            "Powabase %s on %s %s -> responding %d",
            exc.status_code,
            request.method,
            request.url.path,
            503 if retryable else 502,
        )
        if retryable:
            return JSONResponse(
                status_code=503,
                content={"detail": "Upstream service unavailable, please retry."},
                headers={"Retry-After": RETRY_AFTER_SECONDS},
            )
        return JSONResponse(
            status_code=502,
            content={"detail": "Upstream service rejected the request."},
        )


def create_app() -> FastAPI:
    app = FastAPI(title="RAG Chatbot on Powabase", version="1.0.0", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(chat_router)
    app.include_router(sessions_router)
    app.include_router(admin_router)
    app.include_router(auth_router)
    app.include_router(agents_router)
    app.include_router(models_router)
    # The StaticFiles mount at "/" swallows anything registered after it.
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return app


app = create_app()
