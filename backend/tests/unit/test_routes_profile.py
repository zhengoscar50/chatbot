# backend/tests/unit/test_routes_profile.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import profile as profile_route
from app.services.profile_service import get_profile_service


class FakeProfileService:
    def resolve(self, name):
        if name.strip() == "":
            raise ValueError("empty")
        return {"slug": "alice", "kb_id": "kb-1", "agent_id": "agent-1"}


def build_app():
    app = FastAPI()
    app.include_router(profile_route.router)
    app.dependency_overrides[get_profile_service] = lambda: FakeProfileService()
    return app


def test_profile_returns_display_name_and_slug():
    response = TestClient(build_app()).post("/profile", json={"profile": "Alice"})

    assert response.status_code == 200
    assert response.json() == {"profile": "Alice", "slug": "alice"}


def test_profile_does_not_leak_resource_ids():
    body = TestClient(build_app()).post("/profile", json={"profile": "Alice"}).json()

    assert "kb_id" not in body
    assert "agent_id" not in body


def test_profile_returns_422_on_invalid_name():
    class RejectingService(FakeProfileService):
        def resolve(self, name):
            raise ValueError("Profile name must contain at least one letter or number")

    app = FastAPI()
    app.include_router(profile_route.router)
    app.dependency_overrides[get_profile_service] = lambda: RejectingService()

    response = TestClient(app).post("/profile", json={"profile": "!!!"})

    assert response.status_code == 422
