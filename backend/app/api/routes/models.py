from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.services.context_budget import (
    DEFAULT_CONTEXT_TOKENS,
    MIN_CONTEXT_TOKENS,
    context_window,
    max_context_for,
)

router = APIRouter(tags=["models"])


@router.get("/models")
def list_models(user: dict = Depends(get_current_user), settings=Depends(get_settings)):
    """Models offered in the agent form's picker, plus the default.

    Hand-maintained (see Settings.agent_models): Powabase publishes no catalog,
    so this can drift from what the provider serves. The picker keeps an
    "Other…" option and agent creation probes the chosen model, so a stale entry
    surfaces as a clear error rather than a broken agent.
    """
    choices = settings.agent_model_choices
    default = settings.default_agent_model
    # The default must be selectable even if someone trims it out of the list.
    if default and default not in choices:
        choices = [default] + choices
    # The form needs each model's ceiling to bound its slider. Sent from here
    # rather than duplicated in JavaScript, so there is one source of truth and
    # the server still clamps whatever arrives.
    return {
        "models": choices,
        "default": default,
        "context_limits": {
            m: {"window": context_window(m), "max": max_context_for(m)} for m in choices
        },
        "context_default": DEFAULT_CONTEXT_TOKENS,
        "context_min": MIN_CONTEXT_TOKENS,
    }
