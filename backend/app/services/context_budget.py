from __future__ import annotations

# How much retrieved document text one question may be given.
#
# The model's context window is shared between the system prompt, the inlined
# conversation history, the retrieved documents and the answer itself. Spending
# the whole window on retrieval would overflow, so the ceiling here is HALF the
# window: a maxed-out setting cannot fail, without anyone having to reason about
# how long the history happens to be.
#
# Powabase publishes no model catalog and no context-window endpoint — verified
# against the API reference — so this table is hand-maintained and WILL drift,
# exactly like AGENT_MODELS. Two things keep that from becoming a broken agent:
# an unrecognised id falls back to a deliberately small window, and every value
# is clamped server-side rather than trusted from the client.
CONTEXT_WINDOWS = {
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "gpt-5-nano": 400_000,
    "gpt-5-mini": 400_000,
    "gpt-5": 400_000,
    "claude-haiku-4-5": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-5": 200_000,
    "claude-opus-5": 200_000,
    "gemini-2.5-flash": 1_000_000,
}

# An id the table has never seen — the model picker's "Other…" hatch makes that
# expected. Guess low: under-retrieving degrades an answer, over-retrieving
# fails it.
UNKNOWN_MODEL_WINDOW = 32_000

# Powabase documents 1000–128000 for the per-entry knob and states no range for
# the top-level field. Staying inside a documented bound beats discovering the
# real limit as a 400 in the middle of someone's conversation.
ABSOLUTE_MAX_CONTEXT_TOKENS = 128_000
MIN_CONTEXT_TOKENS = 1_000

# What an agent gets when nobody has chosen — the value the app intended before
# this was configurable.
DEFAULT_CONTEXT_TOKENS = 32_000


def context_window(model: str | None) -> int:
    """The model's total context window, or a small guess for unknown ids."""
    return CONTEXT_WINDOWS.get(model or "", UNKNOWN_MODEL_WINDOW)


def max_context_for(model: str | None) -> int:
    """The largest retrieval budget this model may be given."""
    return min(context_window(model) // 2, ABSOLUTE_MAX_CONTEXT_TOKENS)


def clamp_context_tokens(value, model: str | None) -> int:
    """Force a requested budget into the range this model allows.

    Applied on every write, so the slider is a convenience rather than the
    guard, and applied again when an agent's model changes — a value that was
    legal on a 200k model must come down on a 128k one.

    None (or anything non-numeric) means "use the default", itself clamped:
    the default exceeds an unknown model's ceiling, and returning an illegal
    number would defeat the point.
    """
    ceiling = max_context_for(model)
    try:
        wanted = int(value)
    except (TypeError, ValueError):
        wanted = DEFAULT_CONTEXT_TOKENS
    return max(MIN_CONTEXT_TOKENS, min(wanted, ceiling))
