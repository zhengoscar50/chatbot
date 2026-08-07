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
