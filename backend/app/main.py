from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.api.routes.ingest import router as ingest_router
from app.api.routes.sessions import router as sessions_router
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient
from app.core.config import FRONTEND_DIR, get_settings
from app.services.general_kb import ensure_general_kb
from app.services.research_pipeline import ensure_research_pipeline
from app.services.retrieval import reranker_retrieval_config
from app.services.router_agent import ensure_router_agent
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
            router_agent_id = ensure_router_agent(client, settings.router_agent_model)
            research_orchestration_id = ensure_research_pipeline(
                client, settings.research_researcher_model,
                settings.research_analyst_model, settings.research_writer_model,
            )
        except PowabaseAPIError as e:
            raise RuntimeError(f"Powabase is not reachable: {e}") from e
        app.state.powabase_client = client
        app.state.general_kb_id = general_kb_id
        app.state.router_agent_id = router_agent_id
        app.state.research_orchestration_id = research_orchestration_id
        app.state.research_jobs = {}
        app.state.session_service = SessionService(
            client, settings.powabase_agent_model, general_kb_id, reranker_config
        )
        yield
    finally:
        client.close()


def create_app() -> FastAPI:
    app = FastAPI(title="RAG Chatbot on Powabase", version="1.0.0", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(chat_router)
    app.include_router(sessions_router)
    app.include_router(admin_router)
    app.include_router(auth_router)
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return app


app = create_app()
