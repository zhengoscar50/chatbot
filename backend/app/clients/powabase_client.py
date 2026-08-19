from __future__ import annotations

import json
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
        self, name: str, description: str = "",
        indexing_config: dict | None = None, retrieval_config: dict | None = None,
    ) -> dict:
        body: dict = {"name": name, "description": description}
        if indexing_config is not None:
            body["indexing_config"] = indexing_config
        if retrieval_config is not None:
            body["retrieval_config"] = retrieval_config
        response = self._client.post("/api/knowledge-bases", json=body)
        self._raise_for_status(response)
        return response.json()

    def get_knowledge_base(self, kb_id: str) -> dict:
        response = self._client.get(f"/api/knowledge-bases/{kb_id}")
        self._raise_for_status(response)
        return response.json()

    def update_knowledge_base(self, kb_id: str, fields: dict) -> dict:
        response = self._client.patch(f"/api/knowledge-bases/{kb_id}", json=fields)
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
        runtime_knowledge_bases: list | None = None,
        max_context_tokens: int | None = None,
    ) -> list[dict]:
        """Run an agent, streaming.

        `runtime_knowledge_bases` attaches knowledge bases for this one request:
        the agent gets a `knowledge_search` tool over them and decides what to
        search. Entries take `source_ids`, which restricts retrieval to named
        documents inside that KB — the mechanism behind per-chat scratch
        scoping. Streaming only; `/run` rejects the field with 400.

        The context sources are mutually exclusive, so never send both this and
        `context_handler_id`. An empty list is omitted rather than sent, because
        an empty context source is a 400.

        `max_context_tokens` caps the retrieved text. This is the TOP-LEVEL
        field, which Powabase documents as always honored — unlike the
        per-entry knob inside runtime_knowledge_bases, which applies only when
        exactly one knowledge base is in scope.
        """
        payload: dict = {"message": message, "citations_enabled": citations_enabled}
        if session_id:
            payload["session_id"] = session_id
        if runtime_knowledge_bases:
            payload["runtime_knowledge_bases"] = runtime_knowledge_bases
        elif context_handler_id:
            payload["context_handler_id"] = context_handler_id
        if max_context_tokens:
            payload["max_context_tokens"] = max_context_tokens

        # Retry the whole transient family, not just 503. Powabase's gateway
        # returns bare nginx 502s under load, and one of those used to surface
        # to the user as a failed message for a request that would have
        # succeeded a second later.
        for attempt in range(3):
            response = self._client.post(
                f"/api/agents/{agent_id}/run/stream", json=payload, timeout=120.0
            )
            if response.status_code not in (429, 500, 502, 503, 504):
                break
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
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
        """Standalone retrieval with its own `query`, separate from any run.

        No longer used by the chat path, which attaches knowledge bases to the
        run itself (see run_agent's runtime_knowledge_bases). Kept because it is
        the only way to search a string OTHER than the agent's message — if
        agent-formulated search terms ever prove worse than searching the
        question directly, this is the way back.
        """
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

    def list_sessions(self, chatbot_id: str) -> list:
        response = self._client.get(
            "/rest/v1/sessions",
            params={"chatbot_id": f"eq.{chatbot_id}", "order": "updated_at.desc"},
        )
        self._raise_for_status(response)
        return response.json()

    def list_sessions_by_owner(self, owner_id: str) -> list:
        """Every chat a user owns, across all their chatbots.

        list_sessions is scoped by chatbot, which is right for the chat UI but
        wrong for account deletion: a user can own chats in several chatbots,
        and looping chatbot-by-chatbot would require already knowing every
        chatbot the user has, which is exactly the enumeration this replaces.
        Filtering by owner_id directly finds all of them in one call.
        """
        response = self._client.get(
            "/rest/v1/sessions", params={"owner_id": f"eq.{owner_id}"}
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

    def update_agent(self, agent_id: str, fields: dict) -> dict:
        """Patch an agent in place.

        Verified live 2026-08-06: PATCH /api/agents/{id} is supported; PUT and
        POST return 405 with Allow: PATCH, DELETE, HEAD, OPTIONS, GET. Editing
        in place rather than recreating keeps the agent id stable, so existing
        chat threads stay bound to it.
        """
        response = self._client.patch(f"/api/agents/{agent_id}", json=fields)
        self._raise_for_status(response)
        return response.json()

    def remove_source_from_kb(self, kb_id: str, indexed_source_id: str) -> None:
        """Unlink an indexed source from a KB.

        Takes the **indexed-source id** — ``item["id"]`` from
        ``list_kb_sources`` — not ``item["source_id"]``. Verified live: passing
        a source_id returns 404 {"error": "Indexed source not found"}, while the
        indexed-source id returns 200 {"deleted_indexed_source_id": ...}.

        Never deletes the Source itself: upload_source reuses duplicates on 409,
        so one source can belong to several KBs and deleting it would break the
        others.
        """
        response = self._client.delete(
            f"/api/knowledge-bases/{kb_id}/sources/{indexed_source_id}"
        )
        self._raise_for_status(response)

    # Agent rows (PostgREST) -------------------------------------------------
    # The _row suffix is load-bearing: list_agents() above means *Powabase*
    # agents at /api/agents. These talk to our own table at /rest/v1/agents.

    def insert_agent_row(self, row: dict) -> dict:
        response = self._client.post(
            "/rest/v1/agents", json=row, headers={"Prefer": "return=representation"}
        )
        self._raise_for_status(response)
        return response.json()[0]

    def list_agent_rows(self, chatbot_id: str) -> list:
        response = self._client.get(
            "/rest/v1/agents",
            params={"chatbot_id": f"eq.{chatbot_id}", "order": "updated_at.desc"},
        )
        self._raise_for_status(response)
        return response.json()

    def list_agent_rows_by_owner(self, owner_id: str) -> list:
        """Every agent a user owns, across all their chatbots.

        Same reasoning as list_sessions_by_owner: account deletion must find
        every agent the user owns regardless of which chatbot holds it, or it
        strands agents (and the knowledge bases/remote agents they hold) with
        no reachable owner.
        """
        response = self._client.get(
            "/rest/v1/agents", params={"owner_id": f"eq.{owner_id}"}
        )
        self._raise_for_status(response)
        return response.json()

    def list_all_agent_rows(self) -> list:
        """Every agent row in the project, across owners. Used at startup to
        re-sync system prompts; ordinary reads are scoped by owner."""
        response = self._client.get(
            "/rest/v1/agents", params={"order": "created_at.desc"}
        )
        self._raise_for_status(response)
        return response.json()

    def get_agent_row(self, agent_id: str):
        response = self._client.get("/rest/v1/agents", params={"id": f"eq.{agent_id}"})
        # A malformed id (not a valid uuid) → PostgREST 400; it can't match any
        # agent, so treat it as "not found" rather than a server error. The UI
        # can send the literal string "null" when a dialog closes mid-request,
        # which used to surface as a 502.
        if response.status_code == 400:
            return None
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None

    def update_agent_row(self, agent_id: str, fields: dict) -> None:
        response = self._client.patch(
            "/rest/v1/agents", params={"id": f"eq.{agent_id}"}, json=fields
        )
        self._raise_for_status(response)

    def delete_agent_row(self, agent_id: str) -> None:
        response = self._client.delete("/rest/v1/agents", params={"id": f"eq.{agent_id}"})
        self._raise_for_status(response)

    # Chatbot rows (PostgREST) ------------------------------------------------

    def insert_chatbot_row(self, row: dict) -> dict:
        response = self._client.post(
            "/rest/v1/chatbots", json=row, headers={"Prefer": "return=representation"}
        )
        self._raise_for_status(response)
        return response.json()[0]

    def list_chatbot_rows(self, owner_id: str) -> list:
        response = self._client.get(
            "/rest/v1/chatbots",
            params={"owner_id": f"eq.{owner_id}", "order": "created_at.asc"},
        )
        self._raise_for_status(response)
        return response.json()

    def get_chatbot_row(self, chatbot_id: str):
        response = self._client.get(
            "/rest/v1/chatbots", params={"id": f"eq.{chatbot_id}"}
        )
        # A malformed id (not a valid uuid) → PostgREST 400; it cannot match
        # any chatbot, so treat it as "not found" rather than a server error.
        if response.status_code == 400:
            return None
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None

    def update_chatbot_row(self, chatbot_id: str, fields: dict) -> None:
        response = self._client.patch(
            "/rest/v1/chatbots", params={"id": f"eq.{chatbot_id}"}, json=fields
        )
        self._raise_for_status(response)

    def delete_chatbot_row(self, chatbot_id: str) -> None:
        response = self._client.delete(
            "/rest/v1/chatbots", params={"id": f"eq.{chatbot_id}"}
        )
        self._raise_for_status(response)

    # Message rows (PostgREST) ----------------------------------------------
    # The app owns its transcript: a Powabase thread is bound to exactly one
    # agent, so it cannot carry a conversation several agents take turns in.

    def insert_message(self, row: dict) -> dict:
        response = self._client.post(
            "/rest/v1/messages", json=row, headers={"Prefer": "return=representation"}
        )
        self._raise_for_status(response)
        return response.json()[0]

    def list_messages(self, session_id: str) -> list:
        response = self._client.get(
            "/rest/v1/messages",
            params={"session_id": f"eq.{session_id}", "order": "created_at.asc"},
        )
        self._raise_for_status(response)
        return response.json()

    def create_provider_key(self, provider: str, api_key: str) -> dict:
        response = self._client.post(
            "/api/ai-provider-keys", json={"provider": provider, "api_key": api_key}
        )
        self._raise_for_status(response)
        return response.json()


def get_powabase_client(request: Request) -> PowabaseClient:
    """FastAPI dependency returning the shared PowabaseClient created at startup."""
    return request.app.state.powabase_client
