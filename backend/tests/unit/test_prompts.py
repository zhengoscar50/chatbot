from app.services.prompts import OPEN_CLAUSE, STRICT_CLAUSE, compose_system_prompt


def test_strict_appends_the_strict_clause():
    prompt = compose_system_prompt("You are a chemistry tutor.", "strict")
    assert prompt.startswith("You are a chemistry tutor.")
    assert STRICT_CLAUSE in prompt


def test_open_appends_the_open_clause():
    prompt = compose_system_prompt("You are a chemistry tutor.", "open")
    assert OPEN_CLAUSE in prompt
    assert STRICT_CLAUSE not in prompt


def test_user_instructions_are_preserved_verbatim():
    # Whatever the user typed must survive intact — this is their agent's voice.
    instructions = "Speak like a pirate.\n\n  - Always show working\n"
    assert instructions.strip() in compose_system_prompt(instructions, "open")


def test_empty_instructions_yield_the_clause_alone():
    assert compose_system_prompt("", "strict") == STRICT_CLAUSE
    assert compose_system_prompt("   ", "strict") == STRICT_CLAUSE


def test_strict_clause_permits_normal_replies_to_small_talk():
    # Without this, the gate correctly skipping retrieval on "hi" would make a
    # strict agent answer "that isn't in my documents".
    assert "greetings" in STRICT_CLAUSE.lower()


def test_unknown_grounding_falls_back_to_strict():
    assert STRICT_CLAUSE in compose_system_prompt("x", "nonsense")


def test_none_instructions_are_tolerated():
    assert compose_system_prompt(None, "open") == OPEN_CLAUSE
