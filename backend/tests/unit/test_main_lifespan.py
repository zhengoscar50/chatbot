import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.clients.powabase_client import PowabaseAPIError
from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_KB_ID", "kb-123")
    monkeypatch.setenv("POWABASE_AGENT_ID", "agent-456")
    get_settings.cache_clear()


def test_app_starts_when_kb_and_agent_are_reachable(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(
        main_module.PowabaseClient, "get_knowledge_base", lambda self, kb_id: {"id": kb_id}
    )
    monkeypatch.setattr(
        main_module.PowabaseClient, "get_agent", lambda self, agent_id: {"id": agent_id}
    )

    app = main_module.create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert isinstance(app.state.powabase_client, main_module.PowabaseClient)


def test_app_fails_to_start_when_kb_is_unreachable(monkeypatch):
    set_env(monkeypatch)

    def raise_error(self, kb_id):
        raise PowabaseAPIError(404, {"error": "not_found"})

    monkeypatch.setattr(main_module.PowabaseClient, "get_knowledge_base", raise_error)

    app = main_module.create_app()
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
