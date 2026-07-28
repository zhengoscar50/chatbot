import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.clients.powabase_client import PowabaseAPIError
from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    get_settings.cache_clear()


def test_app_starts_when_powabase_reachable(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(
        main_module.PowabaseClient, "list_agents", lambda self: {"agents": []}
    )
    monkeypatch.setattr(main_module, "ensure_general_kb", lambda client: "gkb-1")
    monkeypatch.setattr(main_module, "ensure_router_agent", lambda client, model: "router-1")

    app = main_module.create_app()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert isinstance(app.state.session_service, main_module.SessionService)
        assert isinstance(app.state.powabase_client, main_module.PowabaseClient)
        assert app.state.general_kb_id == "gkb-1"
        assert app.state.session_service.general_kb_id == "gkb-1"
        assert app.state.router_agent_id == "router-1"


def test_app_fails_to_start_when_powabase_unreachable(monkeypatch):
    set_env(monkeypatch)

    def raise_error(self):
        raise PowabaseAPIError(401, {"error": "unauthorized"})

    monkeypatch.setattr(main_module.PowabaseClient, "list_agents", raise_error)

    app = main_module.create_app()
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
