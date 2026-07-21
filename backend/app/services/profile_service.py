from __future__ import annotations

import re
import threading

from fastapi import Request

SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer questions using the linked knowledge "
    "base. If the knowledge base doesn't contain the answer, say so plainly "
    "instead of guessing."
)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _find_by_name(items: list, name: str):
    return next((item for item in items if item.get("name") == name), None)


class ProfileService:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model
        self._cache: dict = {}
        self._cache_lock = threading.Lock()
        self._slug_locks: dict = {}

    def _lock_for(self, slug: str) -> threading.Lock:
        with self._cache_lock:
            lock = self._slug_locks.get(slug)
            if lock is None:
                lock = threading.Lock()
                self._slug_locks[slug] = lock
            return lock

    def resolve(self, name: str) -> dict:
        slug = slugify(name)
        if not slug:
            raise ValueError("Profile name must contain at least one letter or number")

        with self._cache_lock:
            cached = self._cache.get(slug)
        if cached is not None:
            return cached

        with self._lock_for(slug):
            with self._cache_lock:
                cached = self._cache.get(slug)
            if cached is not None:
                return cached
            resolved = self._provision(slug)
            with self._cache_lock:
                self._cache[slug] = resolved
            return resolved

    def _provision(self, slug: str) -> dict:
        kb_name = f"profile-{slug}-kb"
        agent_name = f"profile-{slug}-agent"

        kb = _find_by_name(self.client.list_knowledge_bases().get("items", []), kb_name)
        if kb is None:
            kb = self.client.create_knowledge_base(
                kb_name, description=f"Knowledge base for profile {slug}"
            )

        agent = _find_by_name(self.client.list_agents().get("agents", []), agent_name)
        if agent is None:
            agent = self.client.create_agent(
                agent_name, model=self.model, system_prompt=SYSTEM_PROMPT
            )
            self.client.link_kb_to_agent(agent["id"], kb["id"])

        return {"slug": slug, "kb_id": kb["id"], "agent_id": agent["id"]}


def get_profile_service(request: Request) -> "ProfileService":
    """FastAPI dependency returning the shared ProfileService created at startup."""
    return request.app.state.profile_service
