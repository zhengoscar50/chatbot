from __future__ import annotations

# These clauses must tell the agent to SEARCH, because retrieval is a tool it
# chooses to call rather than context handed to it. They previously read "when
# context from the knowledge base is provided" — accurate when a context
# handler pre-fetched the text and injected it, and silently wrong once
# runtime_knowledge_bases replaced that with a knowledge_search tool. Nothing
# is provided any more, so a prompt that never mentions searching leaves it to
# the model's discretion. Observed live: a chemistry agent whose safety
# handbook was indexed and in scope answered "which building are you in?" from
# its own priors, never calling the tool.
#
# Greetings are still exempt, or a strict agent answers "that isn't in my
# documents" to "hi".
STRICT_CLAUSE = (
    "You have a knowledge_search tool over the documents you were trained on. "
    "For any question of fact, use it before answering — never answer such a "
    "question from memory, even when you believe you know the answer. Base your "
    "answer only on what the search returns, and cite your sources. If the "
    "search does not contain the answer, say so plainly rather than guessing. "
    "Respond normally to greetings and small talk without searching."
)

OPEN_CLAUSE = (
    "You have a knowledge_search tool over the documents you were trained on. "
    "For any question of fact, use it before answering rather than relying on "
    "memory, and cite what you use. If the search does not cover the question, "
    "say what the documents do not cover and then answer normally and "
    "helpfully. Respond normally to greetings and small talk without searching."
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
