from app.services.retrieval_scope import kb_ids_for

AGENT = {"kb_id": "ag-chunk", "kb_full_id": "ag-full"}


def test_agent_permanent_kbs_are_always_in_scope():
    assert kb_ids_for(AGENT, None) == ["ag-chunk", "ag-full"]


def test_legacy_per_chat_kb_is_added_when_present():
    assert kb_ids_for(AGENT, {"kb_id": "sc"}) == ["ag-chunk", "ag-full", "sc"]


def test_no_general_kb_entry_is_ever_emitted():
    # The shared general KB is gone. An agent row left over from before, still
    # carrying the disused flag, must not resurrect it.
    stale = dict(AGENT, use_general_kb=True)
    assert kb_ids_for(stale, None) == ["ag-chunk", "ag-full"]


def test_general_assistant_sees_no_specialist_kbs():
    # agent_row=None is the general assistant. Leaking a specialist's documents
    # into an answer the UI attributes to someone else is the one thing this
    # must never do.
    assert kb_ids_for(None, {"kb_id": "sc"}) == ["sc"]


def test_untrained_agent_with_no_uploads_has_empty_scope():
    # Correct behaviour, not a failure state: the agent answers from the model.
    bare = {"kb_id": None, "kb_full_id": None}
    assert kb_ids_for(bare, {"kb_id": None}) == []


def test_scratch_documents_are_restricted_to_this_chats_source_ids():
    session = {"kb_id": None, "source_ids": ["s1", "s2"]}
    assert kb_ids_for(AGENT, session, scratch_kb_id="scratch") == [
        "ag-chunk", "ag-full", {"id": "scratch", "source_ids": ["s1", "s2"]},
    ]


def test_chat_with_no_uploads_contributes_no_scratch_entry():
    # Emitting the shared scratch KB bare would make EVERY other chat's uploads
    # answerable here. Drop it, never widen it.
    session = {"kb_id": None, "source_ids": []}
    assert kb_ids_for(AGENT, session, scratch_kb_id="scratch") == ["ag-chunk", "ag-full"]


def test_no_duplicates_when_ids_repeat():
    same = {"kb_id": "x", "kb_full_id": "x"}
    assert kb_ids_for(same, {"kb_id": "x"}) == ["x"]


def test_chatbot_knowledge_follows_the_agents_own_kbs():
    assert kb_ids_for(AGENT, None, ["cb-chunk", "cb-full"]) == [
        "ag-chunk", "ag-full", "cb-chunk", "cb-full",
    ]


def test_general_assistant_reads_chatbot_knowledge():
    # Unlike a specialist's permanent tier, chatbot knowledge belongs to the
    # container, so the agent with no row of its own still sees it.
    assert kb_ids_for(None, None, ["cb-chunk"]) == ["cb-chunk"]


def test_every_agent_reads_it_with_no_opt_in():
    # There is no per-agent flag. A row carrying the disused one changes
    # nothing in either direction.
    assert kb_ids_for(dict(AGENT, use_general_kb=False), None, ["cb"]) == [
        "ag-chunk", "ag-full", "cb",
    ]


def test_untrained_chatbot_contributes_nothing():
    assert kb_ids_for(AGENT, None, []) == ["ag-chunk", "ag-full"]


def test_full_order_is_agent_then_chatbot_then_legacy_then_scratch():
    session = {"kb_id": "legacy", "source_ids": ["s1"]}
    assert kb_ids_for(AGENT, session, ["cb"], scratch_kb_id="scratch") == [
        "ag-chunk", "ag-full", "cb", "legacy",
        {"id": "scratch", "source_ids": ["s1"]},
    ]
