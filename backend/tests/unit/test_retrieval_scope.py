from app.services.retrieval_scope import kb_ids_for

AGENT = {"kb_id": "ag-chunk", "kb_full_id": "ag-full", "use_general_kb": False}


def test_agent_permanent_kbs_are_always_in_scope():
    assert kb_ids_for(AGENT, None, "gen") == ["ag-chunk", "ag-full"]


def test_session_scratch_kb_is_added_when_present():
    assert kb_ids_for(AGENT, {"kb_id": "sc"}, "gen") == ["ag-chunk", "ag-full", "sc"]


def test_general_kb_only_when_opted_in():
    opted_in = dict(AGENT, use_general_kb=True)
    assert kb_ids_for(opted_in, None, "gen") == ["ag-chunk", "ag-full", "gen"]


def test_general_kb_omitted_when_opted_in_but_unavailable():
    opted_in = dict(AGENT, use_general_kb=True)
    assert kb_ids_for(opted_in, None, None) == ["ag-chunk", "ag-full"]


def test_untrained_agent_with_no_uploads_has_empty_scope():
    # Correct behavior, not a failure state: the agent answers from the model.
    bare = {"kb_id": None, "kb_full_id": None, "use_general_kb": False}
    assert kb_ids_for(bare, {"kb_id": None}, "gen") == []


def test_order_is_agent_then_scratch_then_general():
    opted_in = dict(AGENT, use_general_kb=True)
    assert kb_ids_for(opted_in, {"kb_id": "sc"}, "gen") == [
        "ag-chunk", "ag-full", "sc", "gen",
    ]


def test_no_duplicates_when_ids_repeat():
    same = {"kb_id": "x", "kb_full_id": "x", "use_general_kb": True}
    assert kb_ids_for(same, {"kb_id": "x"}, "x") == ["x"]


def test_agent_with_only_a_full_document_kb():
    only_full = {"kb_id": None, "kb_full_id": "ag-full", "use_general_kb": False}
    assert kb_ids_for(only_full, None, "gen") == ["ag-full"]


def test_general_assistant_sees_only_the_chat_scratch_and_general_kb():
    # No agent_row means the general assistant is answering. It must NEVER see
    # a specialist's permanent KBs — that would leak one agent's documents into
    # an answer the UI attributes to another.
    assert kb_ids_for(None, {"kb_id": "sc"}, "gen") == ["sc", "gen"]


def test_general_assistant_with_no_chat_uploads():
    assert kb_ids_for(None, None, "gen") == ["gen"]


def test_general_assistant_with_no_general_kb():
    assert kb_ids_for(None, {"kb_id": "sc"}, None) == ["sc"]


# --- shared scratch KB, scoped per chat by source_ids -----------------------

def test_a_chats_uploads_are_scoped_to_its_own_sources():
    """One shared scratch KB serves every chat; source_ids is the isolation."""
    row = {"source_ids": ["src-1", "src-2"]}

    assert kb_ids_for(AGENT, row, "gen", scratch_kb_id="scratch") == [
        "ag-chunk", "ag-full", {"id": "scratch", "source_ids": ["src-1", "src-2"]}
    ]


def test_a_chat_with_no_uploads_gets_no_scratch_scope():
    """Never emit the shared KB unscoped — that would expose every chat's
    uploads to a chat that has uploaded nothing."""
    assert kb_ids_for(AGENT, {"source_ids": []}, "gen", scratch_kb_id="scratch") == [
        "ag-chunk", "ag-full"
    ]
    assert kb_ids_for(AGENT, {}, "gen", scratch_kb_id="scratch") == [
        "ag-chunk", "ag-full"
    ]
    assert kb_ids_for(AGENT, None, "gen", scratch_kb_id="scratch") == [
        "ag-chunk", "ag-full"
    ]


def test_legacy_per_chat_kb_still_works():
    """Chats created before the shared KB keep their own KB; migrating live
    user data is not worth it when both can be searched."""
    row = {"kb_id": "old-chat-kb", "source_ids": []}

    assert kb_ids_for(AGENT, row, "gen", scratch_kb_id="scratch") == [
        "ag-chunk", "ag-full", "old-chat-kb"
    ]


def test_legacy_kb_and_new_sources_can_coexist():
    row = {"kb_id": "old-chat-kb", "source_ids": ["src-1"]}

    assert kb_ids_for(AGENT, row, "gen", scratch_kb_id="scratch") == [
        "ag-chunk", "ag-full", "old-chat-kb",
        {"id": "scratch", "source_ids": ["src-1"]},
    ]


def test_the_general_assistant_sees_scratch_but_no_specialist_kbs():
    row = {"source_ids": ["src-1"]}

    assert kb_ids_for(None, row, "gen", scratch_kb_id="scratch") == [
        {"id": "scratch", "source_ids": ["src-1"]}, "gen"
    ]


# --- the user's personal knowledge base -------------------------------------

def test_the_users_own_knowledge_is_searched_by_their_agents():
    """Trained once by the user, visible to every agent they own — no opt-in."""
    assert kb_ids_for(AGENT, None, "gen", user_kb_ids=["u-chunk", "u-full"]) == [
        "ag-chunk", "ag-full", "u-chunk", "u-full"
    ]


def test_the_general_assistant_also_sees_the_users_knowledge():
    """It is the USER's knowledge, not any agent's, so the fallback sees it too.

    That is deliberately unlike a specialist's permanent KB, which the general
    assistant is blocked from so one agent's documents cannot surface in an
    answer the UI attributes to another.
    """
    assert kb_ids_for(None, None, "gen", user_kb_ids=["u-chunk"]) == ["u-chunk", "gen"]


def test_an_untrained_user_contributes_nothing():
    assert kb_ids_for(AGENT, None, None, user_kb_ids=[]) == ["ag-chunk", "ag-full"]
    assert kb_ids_for(AGENT, None, None, user_kb_ids=None) == ["ag-chunk", "ag-full"]


def test_full_scope_order_is_most_specific_first():
    """Agent's own, then the user's, then this chat's, then shared general."""
    opted_in = dict(AGENT, use_general_kb=True)
    row = {"source_ids": ["src-1"]}

    assert kb_ids_for(opted_in, row, "gen", scratch_kb_id="scratch",
                      user_kb_ids=["u-chunk"]) == [
        "ag-chunk", "ag-full", "u-chunk",
        {"id": "scratch", "source_ids": ["src-1"]}, "gen",
    ]


def test_a_users_knowledge_is_not_duplicated_if_it_repeats():
    assert kb_ids_for(AGENT, None, None, user_kb_ids=["ag-chunk", "u-full"]) == [
        "ag-chunk", "ag-full", "u-full"
    ]
