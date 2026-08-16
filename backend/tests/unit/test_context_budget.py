from app.services.context_budget import (
    ABSOLUTE_MAX_CONTEXT_TOKENS,
    DEFAULT_CONTEXT_TOKENS,
    MIN_CONTEXT_TOKENS,
    UNKNOWN_MODEL_WINDOW,
    clamp_context_tokens,
    context_window,
    max_context_for,
)


def test_known_models_report_their_window():
    assert context_window("gpt-4o-mini") == 128_000
    assert context_window("claude-sonnet-5") == 200_000


def test_an_unknown_model_is_assumed_small():
    """The model list has an "Other..." escape hatch, so an id we have never
    seen is expected. Guess low: under-retrieving degrades an answer, while
    over-retrieving fails it outright."""
    assert context_window("some/experimental-model") == UNKNOWN_MODEL_WINDOW
    assert context_window("") == UNKNOWN_MODEL_WINDOW
    assert context_window(None) == UNKNOWN_MODEL_WINDOW


def test_the_ceiling_is_half_the_window():
    """The window is shared with the system prompt, the inlined history and the
    answer. Half of it leaves guaranteed room, so a maxed-out slider cannot
    overflow."""
    assert max_context_for("gpt-4o-mini") == 64_000
    assert max_context_for("claude-sonnet-5") == 100_000


def test_a_very_large_window_is_still_capped():
    """Powabase documents 1000-128000 for its per-entry knob and no range at
    all for the top-level one. Staying inside a documented bound beats
    discovering the real limit as a 400 mid-conversation."""
    assert max_context_for("gemini-2.5-flash") == ABSOLUTE_MAX_CONTEXT_TOKENS
    assert max_context_for("gpt-5") == ABSOLUTE_MAX_CONTEXT_TOKENS


def test_clamping_holds_the_ceiling():
    assert clamp_context_tokens(999_999, "gpt-4o-mini") == 64_000


def test_clamping_holds_the_floor():
    assert clamp_context_tokens(10, "gpt-4o-mini") == MIN_CONTEXT_TOKENS
    assert clamp_context_tokens(-5, "gpt-4o-mini") == MIN_CONTEXT_TOKENS


def test_a_value_inside_the_range_is_untouched():
    assert clamp_context_tokens(20_000, "gpt-4o-mini") == 20_000


def test_none_means_the_default():
    assert clamp_context_tokens(None, "gpt-4o-mini") == DEFAULT_CONTEXT_TOKENS


def test_the_default_never_exceeds_a_small_models_ceiling():
    """Whatever the default is, it must be legal on the smallest model we
    assume — never returned above the ceiling."""
    ceiling = max_context_for("some/experimental-model")
    assert clamp_context_tokens(None, "some/experimental-model") <= ceiling
    assert clamp_context_tokens(None, "gpt-4o-mini") == DEFAULT_CONTEXT_TOKENS


def test_a_non_numeric_value_falls_back_to_the_default():
    assert clamp_context_tokens("lots", "gpt-4o-mini") == DEFAULT_CONTEXT_TOKENS


def test_moving_to_a_smaller_model_lowers_a_now_illegal_value():
    """100k is legal on a 200k model and illegal on a 128k one."""
    legal_on_claude = clamp_context_tokens(100_000, "claude-sonnet-5")
    assert legal_on_claude == 100_000
    assert clamp_context_tokens(legal_on_claude, "gpt-4o-mini") == 64_000


# --- turning a token budget into a retrieval depth ---------------------------

def test_top_k_divides_the_budget_across_the_sources_in_scope():
    """A question can span several knowledge bases, and top_k is per entry, so
    the budget has to be split — otherwise six sources each retrieve a full
    budget's worth and the total is six times what was asked for."""
    from app.services.context_budget import TOKENS_PER_PASSAGE, top_k_for

    # 8000 tokens over 2 sources, at ~500 tokens a passage -> 8 each.
    assert top_k_for(8_000, 2) == 8
    assert TOKENS_PER_PASSAGE == 500


def test_a_bigger_budget_retrieves_more():
    from app.services.context_budget import top_k_for

    assert top_k_for(4_000, 1) < top_k_for(40_000, 1)


def test_more_sources_means_fewer_passages_each():
    from app.services.context_budget import top_k_for

    assert top_k_for(8_000, 1) > top_k_for(8_000, 4)


def test_every_source_still_contributes_something():
    """A floor, deliberately. Splitting a small budget across many sources can
    round to nothing, and a source silently contributing zero passages is worse
    than slightly exceeding the budget."""
    from app.services.context_budget import MIN_TOP_K, top_k_for

    assert top_k_for(1_000, 6) == MIN_TOP_K
    assert top_k_for(0, 3) == MIN_TOP_K


def test_top_k_is_capped_at_what_powabase_accepts():
    from app.services.context_budget import MAX_TOP_K, top_k_for

    assert top_k_for(10_000_000, 1) == MAX_TOP_K
    assert MAX_TOP_K == 100


def test_no_sources_is_handled():
    from app.services.context_budget import MIN_TOP_K, top_k_for

    assert top_k_for(8_000, 0) == MIN_TOP_K
