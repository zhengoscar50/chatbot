from app.services.reasoning import (
    EFFORT_LEVELS,
    effort_for_model,
    effort_settings,
    supports_effort,
)


def test_only_the_measured_models_support_effort():
    """The list is derived from measurement, not from vendor documentation.
    A model that has not been observed to change behaviour does not get a
    control, because a control that does nothing is worse than none."""
    assert supports_effort("gpt-5") is True
    assert supports_effort("gpt-5-mini") is True
    assert supports_effort("gpt-5-nano") is True

    assert supports_effort("claude-sonnet-5") is False
    assert supports_effort("gemini-2.5-flash") is False
    assert supports_effort("gpt-4o") is False
    assert supports_effort("something-invented") is False
    assert supports_effort(None) is False
    assert supports_effort("") is False


def test_the_three_levels():
    assert EFFORT_LEVELS == ("low", "medium", "high")


def test_settings_carry_the_openai_key():
    assert effort_settings("gpt-5-mini", "high") == {"reasoning_effort": "high"}
    assert effort_settings("gpt-5", "low") == {"reasoning_effort": "low"}


def test_no_effort_means_no_settings():
    """None is the Default level: send nothing and take the vendor's own
    choice, which the spike measured as a genuinely distinct third state."""
    assert effort_settings("gpt-5-mini", None) is None
    assert effort_settings("gpt-5-mini", "") is None


def test_an_unsupported_model_never_produces_settings():
    """Even with a stored effort. Powabase accepts and stores ANY settings key
    without validating it, so a value that reaches the wire for a model that
    ignores it is silently inert — the exact failure this guards against."""
    assert effort_settings("claude-sonnet-5", "high") is None
    assert effort_settings("gemini-2.5-flash", "high") is None
    assert effort_settings("gpt-4o", "high") is None
    assert effort_settings(None, "high") is None


def test_an_unknown_level_is_refused_rather_than_passed_through():
    """A typo must not reach the provider as a literal. It would be stored
    happily and do nothing."""
    assert effort_settings("gpt-5-mini", "extreme") is None
    assert effort_settings("gpt-5-mini", "HIGH") is None


def test_effort_for_model_clears_when_the_model_cannot_use_it():
    """What gets STORED on the row. Changing an agent's model to one without
    effort support must drop the value, or it rides along forever — inert,
    invisible, and confusing to whoever reads the row next. Mirrors how a
    context budget is re-clamped when the model changes."""
    assert effort_for_model("gpt-5-mini", "high") == "high"
    assert effort_for_model("claude-sonnet-5", "high") is None
    assert effort_for_model("gpt-5-mini", "nonsense") is None
    assert effort_for_model("gpt-5-mini", None) is None
