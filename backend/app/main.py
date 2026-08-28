import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.admin import router as admin_router
from app.api.routes.agents import router as agents_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.chatbots import router as chatbots_router
from app.api.routes.health import router as health_router
from app.api.routes.ingest import router as ingest_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.models import router as models_router
from app.api.routes.onboarding import router as onboarding_router
from app.api.routes.sessions import router as sessions_router
from app.api.routes.share import router as share_router
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient
from app.core.config import FRONTEND_DIR, get_settings
from app.services.agent_service import AgentService
from app.services.chatbot_kb import ChatbotKbService
from app.services.chatbot_service import ChatbotService
from app.services.general_assistant import ensure_general_assistant
from app.services.orchestrator import ensure_orchestrator_agent
from app.services.retrieval import reranker_retrieval_config
from app.services.scratch_kb import ensure_scratch_kb
from app.services.session_service import SessionService
from app.services.share_service import ShareService
from app.services.source_names import SourceNameIndex


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
        app.state.scratch_kb_id = scratch_kb_id
        app.state.orchestrator_agent_id = orchestrator_agent_id
        app.state.general_assistant_id = general_assistant_id
        # Registration (auth.py) and the agent/session list & create routes all
        # depend on this — without it they'd 500 on app.state.chatbot_service.
        app.state.chatbot_service = ChatbotService(client)
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
        app.state.chatbot_kb_service = ChatbotKbService(client, reranker_config)
        app.state.session_service = SessionService(
            client, reranker_config, scratch_kb_id
        )
        app.state.share_service = ShareService(client)
        # The filename redaction on the public path reads this. Subscribing it
        # to the client's KB writes is what keeps it exact: a document becomes
        # answerable only through add_source_to_kb, so no upload path can add
        # one the index then fails to re-read.
        source_names = SourceNameIndex(client)
        client.on_kb_write = source_names.invalidate
        app.state.source_names = source_names
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


class RevalidatingStatics(StaticFiles):
    """StaticFiles that always makes the browser check before reusing a file.

    The frontend has no build step and no cache-busting in its script tags, so
    every file is served under a stable URL forever. Without a Cache-Control
    header a browser is free to apply heuristic freshness and reuse a script
    for minutes without asking — which shows up as "I deployed a fix and I do
    not see any change", and costs far more than the revalidation does.

    `no-cache` does not mean "do not cache". It means "cache it, but revalidate
    every time", so the ETag StaticFiles already sends still turns an unchanged
    file into a 304 with no body.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="RAG Chatbot on Powabase", version="1.0.0", lifespan=lifespan)
    register_exception_handlers(app)
    # The widget's loader runs on the HOST page's origin and calls this API, so
    # every one of its requests is cross-origin. Without these headers the
    # browser discards the responses and the panel never loads — while the
    # requests still reach the server, leaving orphaned sessions behind.
    #
    # Any origin is correct here: the point of a share link is that any site may
    # embed it, and every /s/ route is already gated by an unguessable token and
    # a daily cap.
    #
    # allow_headers is the part that keeps this safe app-wide. The authenticated
    # routes need an Authorization header; a cross-origin page cannot send one
    # unless it is allowed here, so their preflight fails and they stay
    # unreachable from other origins. The public routes need only Content-Type.
    # allow_credentials stays False for the same reason it is safe to: this app
    # authenticates with bearer headers, never cookies, so there is no ambient
    # authority for a hostile page to ride.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(chat_router)
    app.include_router(sessions_router)
    app.include_router(admin_router)
    app.include_router(auth_router)
    app.include_router(agents_router)
    app.include_router(chatbots_router)
    app.include_router(knowledge_router)
    app.include_router(models_router)
    app.include_router(share_router)
    app.include_router(onboarding_router)
    # The StaticFiles mount at "/" swallows anything registered after it.
    app.mount("/", RevalidatingStatics(directory=str(FRONTEND_DIR), html=True),
              name="frontend")
    return app


app = create_app()
