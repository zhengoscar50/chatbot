from __future__ import annotations

from app.services.context_budget import top_k_for

# Substrings (checked case-insensitively) that mark a transient
# rate-limit / provider-overload failure — the model is momentarily busy
# rather than the request being wrong. Covers the shapes actually seen from
# OpenRouter/LiteLLM: 429 rate limits and upstream "ResourceExhausted /
# provider_unavailable" overloads.
_THROTTLE_SIGNALS = (
    "rate limit",
    "ratelimit",
    "rate_limit",
    "429",
    "too many requests",
    "resourceexhausted",
    "resource exhausted",
    "resource_exhausted",
    "provider_unavailable",
    "provider unavailable",
    "overloaded",
    "request limit reached",
    "temporarily rate-limited",
)

_MODEL_BUSY_MESSAGE = "The model is busy right now. Please wait a few seconds and try again."


def _looks_throttled(text: str) -> bool:
    lowered = (text or "").lower()
    return any(signal in lowered for signal in _THROTTLE_SIGNALS)


class InsufficientCreditsError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ProviderKeyError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ModelBusyError(Exception):
    """Transient provider rate-limit / overload — retrying shortly should work."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ChatService:
    def __init__(self, client, agent_id, retrieval_kb_ids, top_k=None, max_context_tokens=None):
        self.client = client
        self.agent_id = agent_id
        self.retrieval_kb_ids = retrieval_kb_ids
        self.top_k = top_k
        self.max_context_tokens = max_context_tokens

    def _scope_entries(self) -> list:
        """Build the runtime_knowledge_bases entries for one question.

        An entry is either a bare KB id (search all of it) or a dict carrying
        `source_ids` (search only those documents — how one shared scratch KB
        serves every chat without any chat seeing another's uploads).

        A dict whose source_ids is empty is DROPPED, never widened. Sending
        `{"id": shared_kb}` with no source_ids would search the whole shared
        KB, making every other chat's uploads answerable here. Dropping it
        costs an answer; widening it leaks data.
        """
        entries = []
        for scope in self.retrieval_kb_ids:
            if not scope:
                continue
            if isinstance(scope, dict):
                kb_id = scope.get("id")
                source_ids = scope.get("source_ids")
                if not kb_id or not source_ids:
                    continue
                entries.append({"id": kb_id, "source_ids": list(source_ids)})
            else:
                entries.append({"id": scope})

        # How deep to search each source. Derived from the budget rather than
        # fixed: top_k is the lever that measurably changes what comes back, so
        # a fixed value made the context setting inert. Divided across the
        # entries that SURVIVED — a dropped scope must not still consume a
        # share of the budget.
        depth = top_k_for(self.max_context_tokens, len(entries))
        for entry in entries:
            entry["top_k"] = depth
        return entries

    def ask(self, query: str, message: str | None = None, retrieve: bool = True) -> dict:
        """Answer `query`, showing the agent `message` (defaults to the query).

        The two are separate on purpose. Conversation history is inlined into
        the agent's message — Powabase threads are single-agent, so they cannot
        carry a chat several agents take turns in — but searching with that
        whole blob drowns the question in transcript and degrades retrieval as
        the conversation grows. Retrieval gets the question; the agent gets the
        context.
        """
        knowledge_bases = self._scope_entries()
        # `retrieve` comes from the orchestrator, which decided routing and
        # retrieval in one call. An empty scope still skips retrieval: an agent
        # with nothing to search answers from the model, and Powabase rejects an
        # empty context source with a 400.
        runtime_kbs = knowledge_bases if (retrieve and knowledge_bases) else None

        events = self.client.run_agent(
            self.agent_id, message if message is not None else query,
            citations_enabled=True, runtime_knowledge_bases=runtime_kbs,
            # Only meaningful alongside a context source; nothing is retrieved
            # without one, so there is nothing to cap.
            max_context_tokens=self.max_context_tokens if runtime_kbs else None,
        )
        answer = None
        citations: list = []

        for event in events:
            name = event["event"]
            data = event["data"]
            if name == "error":
                # Standalone error events carry the text under "message" or
                # (as seen live) "error"; fall back to the raw dict only if
                # neither is present, so users never see a Python dict repr.
                self._raise_for_error(
                    data.get("code", ""),
                    data.get("message") or data.get("error") or str(data),
                )
            elif name == "complete":
                # A "complete" event can itself report failure (status:
                # "failed" plus a raw provider error string) rather than a
                # separate "error" event — e.g. a downstream LLM provider
                # rejecting the call. Real answer text lives in "content".
                if data.get("status") == "failed" or data.get("error"):
                    self._raise_for_error("", data.get("error") or "Agent run failed")
                answer = data.get("content")
                citations = data.get("citations", [])

        if not answer:
            raise RuntimeError("Agent run completed without a final answer")

        return {"answer": answer, "citations": citations}

    def _raise_for_error(self, code: str, message: str) -> None:
        if code == "insufficient_credits":
            raise InsufficientCreditsError(message)
        if code == "provider_key_decrypt_failed":
            raise ProviderKeyError(message)
        if _looks_throttled(code) or _looks_throttled(message):
            raise ModelBusyError(_MODEL_BUSY_MESSAGE)
        raise RuntimeError(message)
