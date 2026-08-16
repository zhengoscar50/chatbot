import pytest

from app.clients.powabase_client import PowabaseAPIError
from app.services.agent_service import AgentService, ModelRejectedError
from app.services.prompts import OPEN_CLAUSE, STRICT_CLAUSE


class FakeClient:
    def __init__(self):
        self.created_agents = []
        self.updated_agents = []
        self.rows = {}
        self.kbs = []
        self.deleted_agents = []
        self.deleted_kbs = []
        self.sessions_for_agent = []
        self.deleted_sessions = []
        self.probes = []
        self.probe_agents = []
        self.agent_rows = []
        self.fail_agent_update = None
        self.deleted_probes = []
        self._n = 0

    # Powabase agent API. Throwaway model-probe agents are tracked apart from
    # real ones so they don't shift ids or pollute unrelated assertions.
    def create_agent(self, name, model, system_prompt, settings=None):
        if name.startswith("model-probe-"):
            self.probe_agents.append((name, model))
            return {"id": f"probe-{len(self.probe_agents)}"}
        self.created_agents.append((name, model, system_prompt))
        return {"id": f"pa-{len(self.created_agents)}"}

    def run_agent_sync(self, agent_id, message, response_format=None):
        self.probes.append((agent_id, message))
        return {"content": "OK"}

    def update_agent(self, agent_id, fields):
        if self.fail_agent_update and agent_id == self.fail_agent_update:
            raise PowabaseAPIError(404, {"message": "agent not found"})
        self.updated_agents.append((agent_id, fields))
        return {"id": agent_id}

    def list_all_agent_rows(self):
        return list(self.agent_rows)

    def delete_agent(self, agent_id):
        if str(agent_id).startswith("probe-"):
            self.deleted_probes.append(agent_id)
        else:
            self.deleted_agents.append(agent_id)

    def create_knowledge_base(self, name, description=None, indexing_config=None,
                              retrieval_config=None):
        self._n += 1
        self.kbs.append((name, indexing_config, retrieval_config))
        return {"id": f"kb-{self._n}"}

    def delete_knowledge_base(self, kb_id):
        self.deleted_kbs.append(kb_id)

    # Rows
    def insert_agent_row(self, row):
        row = dict(row, id="ag-1")
        self.rows[row["id"]] = row
        return row

    def list_agent_rows(self, owner_id):
        return [r for r in self.rows.values() if r.get("owner_id") == owner_id]

    def get_agent_row(self, agent_id):
        return self.rows.get(agent_id)

    def update_agent_row(self, agent_id, fields):
        self.rows[agent_id].update(fields)

    def delete_agent_row(self, agent_id):
        self.rows.pop(agent_id, None)

    def delete_session_row(self, session_id):
        self.deleted_sessions.append(session_id)


def test_create_makes_a_powabase_agent_with_the_composed_prompt():
    c = FakeClient()
    row = AgentService(c).create("o1", "Tutor", "Be terse.", "", "gpt-4o-mini", "strict", False)

    name, model, prompt = c.created_agents[0]
    assert model == "gpt-4o-mini"
    assert "Be terse." in prompt and STRICT_CLAUSE in prompt
    assert row["powabase_agent_id"] == "pa-1"
    assert row["owner_id"] == "o1"


def test_create_does_not_provision_knowledge_bases_upfront():
    # KBs are lazy: an agent nobody has trained costs no KB.
    c = FakeClient()
    row = AgentService(c).create("o1", "T", "", "", "m", "strict", False)
    assert c.kbs == []
    assert row["kb_id"] is None and row["kb_full_id"] is None


def test_ensure_kb_creates_chunk_kb_once_and_persists_it():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "", "m", "strict", False)

    first = svc.ensure_kb(row, full_document=False)
    second = svc.ensure_kb(c.get_agent_row("ag-1"), full_document=False)

    assert first == second
    assert len(c.kbs) == 1
    assert c.rows["ag-1"]["kb_id"] == first


def test_ensure_kb_creates_a_separate_full_document_kb():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "", "m", "strict", False)

    chunk = svc.ensure_kb(row, full_document=False)
    full = svc.ensure_kb(c.get_agent_row("ag-1"), full_document=True)

    assert chunk != full
    assert c.kbs[0][1] is None
    assert c.kbs[1][1] == {"strategy": "full_document"}


def test_ensure_kb_passes_the_reranker_config():
    c = FakeClient()
    svc = AgentService(c, reranker_config={"reranker": {"model": "m", "candidate_count": 20}})
    row = svc.create("o1", "T", "", "", "m", "strict", False)
    svc.ensure_kb(row)
    assert c.kbs[0][2] == {"reranker": {"model": "m", "candidate_count": 20}}


def test_get_owned_returns_none_for_another_users_agent():
    c = FakeClient()
    svc = AgentService(c)
    svc.create("o1", "T", "", "", "m", "strict", False)
    assert svc.get_owned("ag-1", "someone-else") is None
    assert svc.get_owned("ag-1", "o1") is not None


def test_list_returns_only_the_owners_agents():
    c = FakeClient()
    svc = AgentService(c)
    svc.create("o1", "T", "", "", "m", "strict", False)
    assert [r["id"] for r in svc.list("o1")] == ["ag-1"]
    assert svc.list("someone-else") == []


def test_update_patches_the_remote_agent_when_prompt_inputs_change():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "Old.", "", "m", "strict", False)

    svc.update(row, {"instructions": "New.", "grounding": "open"})

    agent_id, fields = c.updated_agents[0]
    assert agent_id == "pa-1"
    assert "New." in fields["system_prompt"] and OPEN_CLAUSE in fields["system_prompt"]


def test_update_patches_the_remote_agent_when_model_changes():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "", "m1", "strict", False)

    svc.update(row, {"model": "m2"})

    assert c.updated_agents[0][1]["model"] == "m2"


def test_update_skips_the_remote_call_for_local_only_fields():
    # Renaming or toggling general knowledge changes nothing Powabase knows about.
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "", "m", "strict", False)

    svc.update(row, {"name": "Renamed", "use_general_kb": True})

    assert c.updated_agents == []
    assert c.rows["ag-1"]["name"] == "Renamed"
    assert c.rows["ag-1"]["use_general_kb"] is True


def test_update_returns_the_merged_row():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "Old", "Keep.", "", "m", "strict", False)

    merged = svc.update(row, {"name": "New"})

    assert merged["name"] == "New"
    assert merged["instructions"] == "Keep."


def test_delete_removes_the_permanent_kbs_and_the_remote_agent():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "", "m", "strict", False)
    svc.ensure_kb(row, full_document=False)
    svc.ensure_kb(c.get_agent_row("ag-1"), full_document=True)

    assert svc.delete("ag-1") is True

    assert set(c.deleted_kbs) == {"kb-1", "kb-2"}
    assert c.deleted_agents == ["pa-1"]
    assert c.get_agent_row("ag-1") is None


def test_delete_never_touches_chats():
    # Chats belong to the user, not the agent — the orchestrator picks per
    # message. Deleting one agent must leave every conversation intact,
    # including the turns that agent answered.
    c = FakeClient()
    svc = AgentService(c)
    svc.create("o1", "T", "", "", "m", "strict", False)

    svc.delete("ag-1")

    assert c.deleted_sessions == []


def test_delete_returns_false_for_unknown_agent():
    assert AgentService(FakeClient()).delete("nope") is False


def test_delete_survives_a_failing_remote_cleanup():
    # The row delete is authoritative; a stale remote resource must not block it.
    class Failing(FakeClient):
        def delete_knowledge_base(self, kb_id):
            raise PowabaseAPIError(404, {"error": "gone"})

    c = Failing()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "", "m", "strict", False)
    svc.ensure_kb(row)

    assert svc.delete("ag-1") is True
    assert c.get_agent_row("ag-1") is None





def test_create_probes_the_model_and_cleans_the_probe_up():
    # Powabase accepts any model string and only fails at run time, so a typo
    # would otherwise ship as a silently broken agent.
    c = FakeClient()
    AgentService(c).create("o1", "T", "", "", "gpt-4o-mini", "strict", False)

    assert c.probes, "expected a probe call"
    assert c.probe_agents[0][1] == "gpt-4o-mini"
    # the throwaway must not linger, and the real agent must survive
    assert c.deleted_probes == ["probe-1"]
    assert c.deleted_agents == []


def test_create_rejects_a_model_the_provider_refuses():
    class Refusing(FakeClient):
        def run_agent_sync(self, agent_id, message, response_format=None):
            raise PowabaseAPIError(400, {"error": "unknown model"})

    c = Refusing()
    with pytest.raises(ModelRejectedError) as exc:
        AgentService(c).create("o1", "T", "", "", "not-a-real-model", "strict", False)

    assert exc.value.model == "not-a-real-model"
    # no agent row was written, and the probe agent was cleaned up anyway
    assert c.rows == {}
    assert c.created_agents == []
    assert c.deleted_probes == ["probe-1"]


def test_update_probes_only_when_the_model_actually_changes():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "", "m1", "strict", False)
    c.probes.clear()

    svc.update(row, {"name": "Renamed"})
    assert c.probes == []

    svc.update(row, {"model": "m1"})          # same value
    assert c.probes == []

    svc.update(row, {"model": "m2"})
    assert len(c.probes) == 1


def test_update_leaves_the_agent_untouched_when_the_model_is_refused():
    class Refusing(FakeClient):
        def __init__(self):
            super().__init__()
            self.refuse = False

        def run_agent_sync(self, agent_id, message, response_format=None):
            if self.refuse:
                raise PowabaseAPIError(400, {"error": "unknown model"})
            return {"content": "OK"}

    c = Refusing()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "", "good-model", "strict", False)
    c.refuse = True

    with pytest.raises(ModelRejectedError):
        svc.update(row, {"model": "bad-model"})

    # Validated before patching, so the working agent survives intact.
    assert c.rows["ag-1"]["model"] == "good-model"
    assert c.updated_agents == []


def test_create_persists_the_routing_description():
    c = FakeClient()
    row = AgentService(c).create(
        "o1", "Tutor", "Be terse.", "Answers AP Chemistry questions.",
        "gpt-4o-mini", "strict", False,
    )
    assert row["description"] == "Answers AP Chemistry questions."


def test_description_is_local_only_and_skips_the_remote_patch():
    # The description exists for routing; Powabase knows nothing about it.
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "old desc", "m", "strict", False)
    c.updated_agents.clear()

    svc.update(row, {"description": "new desc"})

    assert c.updated_agents == []
    assert c.rows["ag-1"]["description"] == "new desc"


# --- prompt re-sync ---------------------------------------------------------

def test_resync_prompts_updates_every_agents_system_prompt():
    """Editing a clause in code must actually reach agents that already exist.

    Agent prompts are only patched when instructions or grounding change, so a
    change to the shared grounding clause reached nothing — the same trap
    already recorded for the orchestrator. It mattered when the clause was
    rewritten to tell agents to use their search tool: without a re-sync, every
    existing agent kept the old wording and kept answering from memory.
    """
    c = FakeClient()
    c.agent_rows = [
        {"id": "a1", "powabase_agent_id": "pa-1", "instructions": "Tutor.",
         "grounding": "strict", "model": "m1"},
        {"id": "a2", "powabase_agent_id": "pa-2", "instructions": "",
         "grounding": "open", "model": "m2"},
    ]

    count = AgentService(c).resync_prompts()

    assert count == 2
    sent = dict(c.updated_agents)
    assert "knowledge_search" in sent["pa-1"]["system_prompt"]
    assert "knowledge_search" in sent["pa-2"]["system_prompt"]
    # The model is not touched: a re-sync must not silently re-point an agent.
    assert "model" not in sent["pa-1"]


def test_resync_prompts_survives_one_broken_agent():
    """A single agent whose remote record is gone must not stop the rest."""
    c = FakeClient()
    c.agent_rows = [
        {"id": "a1", "powabase_agent_id": "boom", "instructions": "x",
         "grounding": "strict", "model": "m"},
        {"id": "a2", "powabase_agent_id": "pa-2", "instructions": "y",
         "grounding": "strict", "model": "m"},
    ]
    c.fail_agent_update = "boom"

    assert AgentService(c).resync_prompts() == 1


# --- context budget ---------------------------------------------------------

def test_creating_an_agent_clamps_an_oversized_budget():
    """The slider is a convenience; the server is the guard. A client can post
    anything."""
    c = FakeClient()
    row = AgentService(c).create("o1", "T", "", "", "gpt-4o-mini", "strict", False,
                                 max_context_tokens=999_999)
    assert row["max_context_tokens"] == 64_000        # half of 128k


def test_creating_an_agent_defaults_the_budget():
    from app.services.context_budget import DEFAULT_CONTEXT_TOKENS
    c = FakeClient()
    row = AgentService(c).create("o1", "T", "", "", "gpt-4o-mini", "strict", False)
    assert row["max_context_tokens"] == DEFAULT_CONTEXT_TOKENS


def test_moving_to_a_smaller_model_lowers_the_budget():
    """100k is legal on a 200k model and illegal on a 128k one. Without this,
    an edit that only changes the model leaves an out-of-range value behind."""
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "", "claude-sonnet-5", "strict", False,
                     max_context_tokens=100_000)
    assert row["max_context_tokens"] == 100_000

    merged = svc.update(row, {"model": "gpt-4o-mini"})

    assert merged["max_context_tokens"] == 64_000
    # ...and persisted, not just returned.
    assert c.rows[row["id"]]["max_context_tokens"] == 64_000


def test_editing_only_the_budget_clamps_it():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "", "gpt-4o-mini", "strict", False)

    merged = svc.update(row, {"max_context_tokens": 999_999})

    assert merged["max_context_tokens"] == 64_000
