from app.services.onboarding import STEP_IDS, derive_steps


def done_map(chatbots, agents, has_answer):
    return {s["id"]: s["done"] for s in derive_steps(chatbots, agents, has_answer)}


def test_a_fresh_account_has_only_the_chatbot_step_ticked():
    """Signup creates one chatbot and nothing else. That single tick is
    deliberate: it shows what a done row looks like before you have earned one."""
    d = done_map([{"id": "cb1"}], [], False)

    assert d == {"chatbot": True, "agent": False, "description": False,
                 "knowledge": False, "answer": False}


def test_an_account_with_no_chatbot_at_all_ticks_nothing():
    assert not any(s["done"] for s in derive_steps([], [], False))


def test_an_agent_without_a_description_leaves_the_description_step_open():
    """The whole point of the panel. Routing matches the user's message against
    agent descriptions, so an agent with none is never chosen and nothing says
    why. Step 2 ticks, step 3 must not."""
    d = done_map([{"id": "cb1"}], [{"id": "a1", "description": ""}], False)

    assert d["agent"] is True
    assert d["description"] is False


def test_whitespace_is_not_a_description():
    d = done_map([{"id": "cb1"}], [{"id": "a1", "description": "   \n"}], False)
    assert d["description"] is False


def test_a_missing_description_key_is_not_a_description():
    """PostgREST omits nothing here today, but a null column arrives as None
    and `None.strip()` would be a 500 on the dashboard's first paint."""
    d = done_map([{"id": "cb1"}], [{"id": "a1"}], False)
    assert d["description"] is False

    d = done_map([{"id": "cb1"}], [{"id": "a1", "description": None}], False)
    assert d["description"] is False


def test_one_described_agent_among_many_is_enough():
    agents = [{"id": "a1", "description": ""}, {"id": "a2", "description": "Chemistry"}]
    assert done_map([{"id": "cb1"}], agents, False)["description"] is True


def test_knowledge_ticks_from_an_agent_kb():
    d = done_map([{"id": "cb1"}], [{"id": "a1", "description": "x", "kb_id": "k1"}], False)
    assert d["knowledge"] is True


def test_knowledge_ticks_from_an_agents_full_kb():
    """Full-document retrieval stores its own kb id. An agent trained only that
    way is still trained."""
    d = done_map([{"id": "cb1"}],
                 [{"id": "a1", "description": "x", "kb_full_id": "k2"}], False)
    assert d["knowledge"] is True


def test_knowledge_ticks_from_chatbot_wide_knowledge():
    """Chatbot knowledge is read by every agent automatically, so a document
    uploaded there counts as training even with no agent KB anywhere."""
    d = done_map([{"id": "cb1", "kb_id": "k3"}], [{"id": "a1", "description": "x"}], False)
    assert d["knowledge"] is True


def test_an_empty_string_kb_id_is_not_knowledge():
    d = done_map([{"id": "cb1", "kb_id": ""}], [{"id": "a1", "kb_id": ""}], False)
    assert d["knowledge"] is False


def test_the_answer_step_comes_straight_from_the_flag():
    assert done_map([{"id": "cb1"}], [], True)["answer"] is True
    assert done_map([{"id": "cb1"}], [], False)["answer"] is False


def test_steps_are_always_five_in_a_fixed_order():
    """The frontend renders them in the order given and the DOM tests index by
    position, so order is part of the contract."""
    steps = derive_steps([], [], False)

    assert [s["id"] for s in steps] == list(STEP_IDS)
    assert len(steps) == 5


def test_every_step_carries_non_empty_copy():
    """The server owns all copy — the panel renders whatever arrives, so an
    empty label ships an empty row rather than falling back to anything."""
    for s in derive_steps([], [], False):
        assert s["label"].strip()
        assert s["hint"].strip()


def test_a_described_agent_with_no_document_leaves_the_knowledge_step_open():
    """Described and routable, but with nothing to retrieve from. The agent
    will be chosen and will then answer from the model alone — which is the
    failure this step exists to prevent."""
    d = done_map([{"id": "cb1"}], [{"id": "a1", "description": "Chemistry"}], False)

    assert d["description"] is True
    assert d["knowledge"] is False


def test_deleting_the_last_agent_un_ticks_its_steps():
    """The reason this is derived rather than stored. A flag set when the agent
    was created would still claim it exists, and the panel would be lying at
    exactly the moment someone needs it to be honest."""
    agents = [{"id": "a1", "description": "Chemistry", "kb_id": "k1"}]
    before = done_map([{"id": "cb1"}], agents, False)
    after = done_map([{"id": "cb1"}], [], False)

    assert (before["agent"], before["description"], before["knowledge"]) == (True, True, True)
    assert (after["agent"], after["description"], after["knowledge"]) == (False, False, False)


def test_the_description_hint_explains_routing():
    """This hint is the feature. If it does not say why a description matters,
    the panel has not solved the problem it exists for."""
    hint = next(s["hint"] for s in derive_steps([], [], False) if s["id"] == "description")

    assert "rout" in hint.lower()
