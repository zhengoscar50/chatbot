from __future__ import annotations

# The grounding clause is scoped to "when context is provided" on purpose. The
# retrieval gate correctly skips retrieval for greetings and small talk, so an
# unconditional "only answer from documents" would make a strict agent reply
# "that isn't in my documents" to "hi".
STRICT_CLAUSE = (
    "When context from the knowledge base is provided with a question, base your "
    "answer only on that context and cite your sources. If the provided context "
    "does not contain the answer, say so plainly rather than guessing. Respond "
    "normally to greetings and small talk."
)

OPEN_CLAUSE = (
    "When context from the knowledge base is provided with a question, use it and "
    "cite your sources. When no context is provided, or it does not cover the "
    "question, answer normally and helpfully."
)


def compose_system_prompt(instructions: str, grounding: str) -> str:
    """The user's instructions plus a grounding clause.

    The instructions are preserved verbatim — they are the agent's voice, and
    the user wrote them. Unknown grounding values fall back to strict: the safer
    default for a RAG agent is to refuse rather than to invent.
    """
    clause = OPEN_CLAUSE if grounding == "open" else STRICT_CLAUSE
    base = (instructions or "").strip()
    return f"{base}\n\n{clause}" if base else clause
