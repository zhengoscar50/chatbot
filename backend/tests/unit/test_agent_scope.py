from app.services.agent_scope import roster_for, sanitise_exclusions

ROSTER = [
    {"id": "a1", "name": "Chem"},
    {"id": "a2", "name": "Contracts"},
    {"id": "a3", "name": "HR"},
]


def test_a_chat_starts_with_every_agent():
    assert roster_for(ROSTER, None) == ROSTER
    assert roster_for(ROSTER, []) == ROSTER


def test_excluded_agents_are_kept_out():
    kept = roster_for(ROSTER, ["a2"])
    assert [a["id"] for a in kept] == ["a1", "a3"]


def test_excluding_everything_is_allowed():
    """Not an error: it is how a chat says "just the general assistant". The
    orchestrator already returns the general assistant for an empty roster
    without paying for a routing call."""
    assert roster_for(ROSTER, ["a1", "a2", "a3"]) == []


def test_a_stale_id_is_harmless():
    """An agent can be deleted while a chat still lists it. Filtering is a
    membership test, so a stale id simply matches nothing."""
    assert roster_for(ROSTER, ["deleted-agent"]) == ROSTER


def test_the_original_roster_is_not_mutated():
    roster_for(ROSTER, ["a1"])
    assert len(ROSTER) == 3


# --- what may be stored -----------------------------------------------------

def test_only_ids_the_user_owns_are_stored():
    """A client can send anything. Storing a foreign id would leak nothing by
    itself, but it would let one user's chat reference another's agent."""
    assert sanitise_exclusions(["a1", "someone-elses"], ROSTER) == ["a1"]


def test_duplicates_are_collapsed():
    assert sanitise_exclusions(["a1", "a1", "a2"], ROSTER) == ["a1", "a2"]


def test_order_follows_the_roster_not_the_request():
    """Stable storage: the same selection always serialises the same way, so a
    no-op save does not look like a change."""
    assert sanitise_exclusions(["a3", "a1"], ROSTER) == ["a1", "a3"]


def test_junk_is_dropped():
    assert sanitise_exclusions(None, ROSTER) == []
    assert sanitise_exclusions("a1", ROSTER) == []
    assert sanitise_exclusions([None, 5, {"id": "a1"}], ROSTER) == []


def test_pruning_drops_ids_no_longer_in_the_roster():
    """Called when an agent is deleted, so exclusion lists do not accumulate
    ids that can never match again."""
    assert sanitise_exclusions(["a1", "gone"], ROSTER) == ["a1"]
