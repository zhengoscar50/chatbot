from __future__ import annotations

import time

import httpx
from fastapi import Request

from app.clients.sse import parse_sse


class PowabaseAPIError(Exception):
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Powabase API error {status_code}: {body}")


class PowabaseClient:
    def __init__(self, base_url: str, service_role_key: str):
        headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
        }
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=30.0
        )

    def close(self) -> None:
        self._client.close()

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise PowabaseAPIError(response.status_code, body)

    # Sources -----------------------------------------------------------

    def upload_source(self, filename: str, content: bytes) -> dict:
        response = self._client.post(
            "/api/sources/upload", files={"file": (filename, content)}
        )
        if response.status_code == 409:
            body = response.json()
            return body.get("duplicate", body)
        self._raise_for_status(response)
        return response.json()

    def get_source(self, source_id: str) -> dict:
        response = self._client.get(f"/api/sources/{source_id}")
        self._raise_for_status(response)
        return response.json()

    # Knowledge bases -----------------------------------------------------

    def list_knowledge_bases(self) -> dict:
        response = self._client.get("/api/knowledge-bases")
        self._raise_for_status(response)
        return response.json()

    def create_knowledge_base(
        self, name: str, description: str = "", indexing_config: dict | None = None
    ) -> dict:
        body: dict = {"name": name, "description": description}
        if indexing_config is not None:
            body["indexing_config"] = indexing_config
        response = self._client.post("/api/knowledge-bases", json=body)
        self._raise_for_status(response)
        return response.json()

    def get_knowledge_base(self, kb_id: str) -> dict:
        response = self._client.get(f"/api/knowledge-bases/{kb_id}")
        self._raise_for_status(response)
        return response.json()

    def add_source_to_kb(self, kb_id: str, source_id: str) -> dict:
        response = self._client.post(
            f"/api/knowledge-bases/{kb_id}/sources", json={"source_id": source_id}
        )
        self._raise_for_status(response)
        return response.json()

    def list_kb_sources(self, kb_id: str) -> dict:
        response = self._client.get(f"/api/knowledge-bases/{kb_id}/sources")
        self._raise_for_status(response)
        return response.json()

    # Agents --------------------------------------------------------------

    def list_agents(self) -> dict:
        response = self._client.get("/api/agents")
        self._raise_for_status(response)
        return response.json()

    def create_agent(self, name: str, model: str, system_prompt: str, settings: dict | None = None) -> dict:
        body = {"name": name, "model": model, "system_prompt": system_prompt}
        if settings is not None:
            body["settings"] = settings
        response = self._client.post(
            "/api/agents",
            json=body,
        )
        self._raise_for_status(response)
        return response.json()

    def get_agent(self, agent_id: str) -> dict:
        response = self._client.get(f"/api/agents/{agent_id}")
        self._raise_for_status(response)
        return response.json()

    def link_kb_to_agent(self, agent_id: str, kb_id: str) -> dict:
        response = self._client.post(
            f"/api/agents/{agent_id}/knowledge-bases",
            json={"knowledge_base_id": kb_id},
        )
        self._raise_for_status(response)
        return response.json()

    def run_agent(
        self,
        agent_id: str,
        message: str,
        session_id: str | None = None,
        citations_enabled: bool = True,
        context_handler_id: str | None = None,
    ) -> list[dict]:
        payload: dict = {"message": message, "citations_enabled": citations_enabled}
        if session_id:
            payload["session_id"] = session_id
        if context_handler_id:
            payload["context_handler_id"] = context_handler_id

        response = self._client.post(
            f"/api/agents/{agent_id}/run/stream", json=payload, timeout=120.0
        )
        if response.status_code == 503:
            time.sleep(1.0)
            response = self._client.post(
                f"/api/agents/{agent_id}/run/stream", json=payload, timeout=120.0
            )
        self._raise_for_status(response)
        return parse_sse(response.text)

    def run_agent_sync(self, agent_id: str, message: str, response_format: dict | None = None) -> dict:
        payload: dict = {"message": message}
        if response_format is not None:
            payload["response_format"] = response_format
        response = self._client.post(f"/api/agents/{agent_id}/run", json=payload, timeout=60.0)
        self._raise_for_status(response)
        return response.json()

    # Context handlers -------------------------------------------------------

    def create_context_handler(
        self, query: str, knowledge_bases: list, max_context_tokens: int | None = None
    ) -> dict:
        body: dict = {"query": query, "knowledge_bases": knowledge_bases}
        if max_context_tokens is not None:
            body["max_context_tokens"] = max_context_tokens
        response = self._client.post("/api/context-handlers", json=body, timeout=60.0)
        self._raise_for_status(response)
        return response.json()

    # Sessions table (PostgREST) -------------------------------------------

    def insert_session(self, row: dict) -> dict:
        response = self._client.post(
            "/rest/v1/sessions",
            json=row,
            headers={"Prefer": "return=representation"},
        )
        self._raise_for_status(response)
        created = response.json()
        return created[0] if isinstance(created, list) else created

    def list_sessions(self, owner_id: str) -> list:
        response = self._client.get(
            "/rest/v1/sessions",
            params={"owner_id": f"eq.{owner_id}", "order": "updated_at.desc"},
        )
        self._raise_for_status(response)
        return response.json()

    def get_session_row(self, session_id: str):
        response = self._client.get(
            "/rest/v1/sessions", params={"id": f"eq.{session_id}"}
        )
        # A malformed id (not a valid uuid) → PostgREST 400; it can't match any
        # session, so treat it as "not found" rather than a server error.
        if response.status_code == 400:
            return None
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None

    def update_session(self, session_id: str, fields: dict) -> None:
        response = self._client.patch(
            "/rest/v1/sessions", params={"id": f"eq.{session_id}"}, json=fields
        )
        self._raise_for_status(response)

    def get_session_messages(self, powabase_session_id: str) -> dict:
        response = self._client.get(f"/api/sessions/{powabase_session_id}/messages")
        self._raise_for_status(response)
        return response.json()

    def delete_session_row(self, session_id: str) -> None:
        response = self._client.delete(
            "/rest/v1/sessions", params={"id": f"eq.{session_id}"}
        )
        self._raise_for_status(response)

    # Users (PostgREST) -------------------------------------------------------

    def insert_user(self, row: dict) -> dict:
        response = self._client.post(
            "/rest/v1/users", json=row, headers={"Prefer": "return=representation"}
        )
        self._raise_for_status(response)
        created = response.json()
        return created[0] if isinstance(created, list) else created

    def get_user_by_username(self, username: str) -> dict | None:
        response = self._client.get(
            "/rest/v1/users", params={"username": f"eq.{username}"}
        )
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None

    def get_user(self, user_id: str) -> dict | None:
        response = self._client.get("/rest/v1/users", params={"id": f"eq.{user_id}"})
        if response.status_code == 400:  # malformed uuid -> treat as not found
            return None
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None

    def list_users(self) -> list:
        response = self._client.get("/rest/v1/users", params={"order": "created_at.desc"})
        self._raise_for_status(response)
        return response.json()

    def list_all_sessions(self) -> list:
        response = self._client.get("/rest/v1/sessions", params={"order": "updated_at.desc"})
        self._raise_for_status(response)
        return response.json()

    def update_user(self, user_id: str, fields: dict) -> None:
        response = self._client.patch(
            "/rest/v1/users", params={"id": f"eq.{user_id}"}, json=fields
        )
        self._raise_for_status(response)

    def delete_user(self, user_id: str) -> None:
        response = self._client.delete("/rest/v1/users", params={"id": f"eq.{user_id}"})
        self._raise_for_status(response)

    # Knowledge base / agent deletion --------------------------------------

    def delete_knowledge_base(self, kb_id: str) -> None:
        response = self._client.delete(f"/api/knowledge-bases/{kb_id}")
        self._raise_for_status(response)

    def delete_agent(self, agent_id: str) -> None:
        response = self._client.delete(f"/api/agents/{agent_id}")
        self._raise_for_status(response)

    # Provider keys ---------------------------------------------------------

    def create_provider_key(self, provider: str, api_key: str) -> dict:
        response = self._client.post(
            "/api/ai-provider-keys", json={"provider": provider, "api_key": api_key}
        )
        self._raise_for_status(response)
        return response.json()


def get_powabase_client(request: Request) -> PowabaseClient:
    """FastAPI dependency returning the shared PowabaseClient created at startup."""
    return request.app.state.powabase_client
