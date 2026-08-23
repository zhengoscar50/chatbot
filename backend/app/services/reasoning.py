"""Per-agent reasoning effort, for the models that actually honour it.

Powabase passes an agent's `settings` straight through to the provider WITHOUT
validating it. A misspelled key, or a key the provider ignores, is accepted,
stored, and returned as if it worked — so an effort control shown on a model
that ignores it would look saved and do nothing.

The supported list below is therefore derived from MEASUREMENT, not from vendor
documentation. Measured live on 2026-08-22, same question, same prompt:

    gpt-5-mini  reasoning_effort=low    1.4s   33 completion    0 reasoning
    gpt-5-mini  (unset)                 8.1s  970 completion    — reasoning
    gpt-5-mini  reasoning_effort=high   5.4s  182 completion  128 reasoning

    claude-sonnet-5  (unset)                          5.5s  0 reasoning
    claude-sonnet-5  thinking={type,budget_tokens}    7.0s  0 reasoning
    claude-sonnet-5  thinking + max_tokens            7.0s  0 reasoning
    claude-sonnet-5  reasoning_effort                 6.8s  0 reasoning
    claude-sonnet-5  max_thinking_tokens              6.7s  0 reasoning
    gemini-2.5-flash thinking_budget                  no usage reported at all

Four Anthropic key shapes produced no reasoning tokens and no latency change,
so Anthropic and Gemini are deliberately absent. Do not add a model here
because its vendor documents a reasoning parameter — add it because you ran
the check in `tools/verify_reasoning_effort.py` and watched the numbers move.
"""
from __future__ import annotations

EFFORT_LEVELS = ("low", "medium", "high")

# Models observed to change behaviour when `reasoning_effort` is set.
SUPPORTS_EFFORT = frozenset({"gpt-5", "gpt-5-mini", "gpt-5-nano"})


def supports_effort(model: str | None) -> bool:
    """Whether this model honours a reasoning-effort setting."""
    return (model or "") in SUPPORTS_EFFORT


def effort_for_model(model: str | None, effort: str | None) -> str | None:
    """The effort worth STORING on an agent row for this model.

    Returns None when the model cannot use it, so changing an agent's model to
    one without support drops the value rather than leaving it to ride along
    invisibly. This mirrors how a context budget is re-clamped when the model
    changes: the stored value never outlives the model that justified it.
    """
    if not supports_effort(model):
        return None
    return effort if effort in EFFORT_LEVELS else None


def effort_settings(model: str | None, effort: str | None) -> dict | None:
    """The `settings` payload for Powabase, or None to send nothing.

    None is the Default level — the vendor picks, which the measurements above
    show is a genuinely distinct third state rather than a synonym for medium.
    """
    level = effort_for_model(model, effort)
    return {"reasoning_effort": level} if level else None
