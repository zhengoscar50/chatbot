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


def test_clauses_tell_the_agent_to_search():
    """Retrieval is a tool the model chooses to call, not injected context.

    These clauses were written when context was pre-fetched and handed over
    ("when context is provided..."). Under runtime_knowledge_bases nothing is
    provided — the agent gets a knowledge_search tool — so a prompt that never
    mentions searching leaves it to chance. Observed live: a chemistry agent
    with its handbook indexed answered "which building are you in?" from model
    priors, never calling the tool.
    """
    for clause in (STRICT_CLAUSE, OPEN_CLAUSE):
        assert "search" in clause.lower()
        assert "knowledge_search" in clause


def test_strict_clause_forbids_answering_facts_from_memory():
    lowered = STRICT_CLAUSE.lower()
    assert "before answering" in lowered or "before you answer" in lowered
    assert "memory" in lowered
