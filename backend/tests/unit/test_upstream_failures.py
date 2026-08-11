# backend/tests/unit/test_upstream_failures.py
"""A Powabase blip must not look like a crash in this app.

On 2026-08-08 Powabase answered one `get_user` call with 502 Bad Gateway. The
call sits in `get_current_user`, the auth dependency behind every authenticated
endpoint, and nothing caught PowabaseAPIError — so the request became a 500
with a full traceback, as though this app had failed. The next poll four
seconds later succeeded.

500 is the wrong answer twice over: it tells the client the failure is
permanent and ours, and it sends whoever reads the log debugging this codebase
for someone else's outage.
"""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes.auth import router as auth_router
from app.clients.powabase_client import PowabaseAPIError, get_powabase_client
from app.core.config import get_settings
from app.core.security import create_access_token

SECRET = "test-jwt-secret-not-a-real-one"
USER_ID = "11111111-1111-1111-1111-111111111111"


class BlippingClient:
    """Every call fails the way a struggling upstream fails."""

    def __init__(self, status_code, body="<html><body>502 Bad Gateway</body></html>"):
        self.status_code = status_code
        self.body = body

    def _blip(self, *a, **kw):
        raise PowabaseAPIError(self.status_code, self.body)

    get_user = _blip
    get_user_by_username = _blip
    create_user = _blip


def build_app(client, with_handlers=True):
    from app.main import register_exception_handlers

    app = FastAPI()
    if with_handlers:
        register_exception_handlers(app)
    app.include_router(auth_router)

    @app.get("/protected")
    def protected(user: dict = Depends(get_current_user)):
        return {"ok": True}

    app.dependency_overrides[get_powabase_client] = lambda: client
    return app


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def auth_header():
    return {"Authorization": "Bearer " + create_access_token(USER_ID, SECRET, 1)}


@pytest.mark.parametrize("upstream", [500, 502, 503, 504, 429])
def test_transient_upstream_failure_is_503_not_500(upstream):
    """5xx and rate limits are retryable: say so, and never say 500."""
    app = build_app(BlippingClient(upstream))

    response = TestClient(app, raise_server_exceptions=False).get(
        "/protected", headers=auth_header()
    )

    assert response.status_code == 503
    assert response.headers.get("Retry-After")


def test_upstream_4xx_is_502():
    """A rejected request is not transient — retrying will not help."""
    app = build_app(BlippingClient(400, {"message": "malformed"}))

    response = TestClient(app, raise_server_exceptions=False).get(
        "/protected", headers=auth_header()
    )

    assert response.status_code == 502
    assert "Retry-After" not in response.headers


def test_login_survives_an_upstream_blip():
    """The worst case: an outage that also blocks signing back in."""
    app = build_app(BlippingClient(502))

    response = TestClient(app, raise_server_exceptions=False).post(
        "/auth/login", json={"username": "alice", "password": "pw-12345678"}
    )

    assert response.status_code == 503


def test_upstream_body_is_not_echoed_to_the_client():
    """Upstream HTML and internal detail stay in the log, not the response."""
    app = build_app(BlippingClient(502, "<html>nginx/1.24.0 internal-host</html>"))

    response = TestClient(app, raise_server_exceptions=False).get(
        "/protected", headers=auth_header()
    )

    assert "nginx" not in response.text
    assert "html" not in response.text.lower()


def test_a_genuine_401_is_still_a_401():
    """The handler must not swallow ordinary auth failures."""
    app = build_app(BlippingClient(502))

    response = TestClient(app, raise_server_exceptions=False).get("/protected")

    assert response.status_code == 401


def test_without_the_handler_it_is_a_500():
    """Characterises the bug, so a future refactor cannot quietly restore it."""
    app = build_app(BlippingClient(502), with_handlers=False)

    response = TestClient(app, raise_server_exceptions=False).get(
        "/protected", headers=auth_header()
    )

    assert response.status_code == 500


def test_the_real_app_registers_the_handler(monkeypatch):
    """Every other test builds its own app, so none of them would notice the
    registration being dropped from create_app — which is the only place that
    matters in production."""
    monkeypatch.setenv("POWABASE_BASE_URL", "https://tests.invalid")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "k")
    get_settings.cache_clear()
    from app.main import create_app

    assert PowabaseAPIError in create_app().exception_handlers
