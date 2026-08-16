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

# What an agent gets when nobody has chosen. 8k reproduces the behaviour the
# app actually had — 8 passages each from a typical two-source scope — rather
# than the 32k it nominally configured and never applied.
DEFAULT_CONTEXT_TOKENS = 8_000

# The budget is spent by turning it into `top_k`, the number of passages
# retrieved per knowledge base.
#
# This is the lever that measurably works. Sending the top-level
# max_context_tokens changed nothing at all: with the same question and
# top_k=20, budgets of 1k / 8k / 128k produced 19 / 15 / 18 citations — no
# trend, and 19 passages cannot fit in 1000 tokens. Varying top_k over 2 / 8 /
# 40 produced exactly 2 / 8 / 9 citations. Powabase documents the top-level
# field as always honored, which may hold for pre-fetched context; it does not
# appear to bind the agent-driven knowledge_search tool this app now uses.
#
# A rough average passage size is enough: the point is that the slider changes
# retrieval depth predictably and monotonically, not that the token arithmetic
# is exact.
TOKENS_PER_PASSAGE = 500

# Powabase documents top_k as 1-100.
MAX_TOP_K = 100

# Every source in scope contributes at least this much. Splitting a small
# budget across six knowledge bases rounds to zero otherwise, and a source
# silently contributing nothing is worse than slightly overrunning the budget.
MIN_TOP_K = 2


def top_k_for(budget_tokens: int, kb_count: int) -> int:
    """How many passages to take from EACH knowledge base in scope.

    top_k is per entry, so the budget is divided by the number of sources —
    otherwise six of them each retrieve a full budget's worth and the question
    costs six times what was asked for.
    """
    if kb_count <= 0:
        return MIN_TOP_K
    per_source = int(budget_tokens or 0) // (kb_count * TOKENS_PER_PASSAGE)
    return max(MIN_TOP_K, min(per_source, MAX_TOP_K))


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
