from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.config import get_settings

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
    return {"models": choices, "default": default}
