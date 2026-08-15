import pytest

from app.clients.powabase_client import PowabaseAPIError
from app.services.user_kb import UserKbService


class FakeClient:
    def __init__(self, rows=None):
        self.rows = {r["id"]: r for r in (rows or [])}
        self.created_kbs = []
        self.updated_users = []
        self.kb_items = {}
        self.removed = []

    def get_user(self, user_id):
        return self.rows.get(user_id)

    def update_user(self, user_id, fields):
        self.updated_users.append((user_id, fields))
        self.rows[user_id].update(fields)

    def create_knowledge_base(self, name, description="", indexing_config=None,
                              retrieval_config=None):
        kb = {"id": "kb-%d" % (len(self.created_kbs) + 1), "name": name,
              "indexing_config": indexing_config, "retrieval_config": retrieval_config}
        self.created_kbs.append(kb)
        return kb

    def list_kb_sources(self, kb_id):
        return {"items": self.kb_items.get(kb_id, [])}

    def remove_source_from_kb(self, kb_id, indexed_source_id):
        self.removed.append((kb_id, indexed_source_id))

    def delete_knowledge_base(self, kb_id):
        self.rows and None


def user(**over):
    row = {"id": "u1", "username": "alice", "kb_id": None, "kb_full_id": None}
    row.update(over)
    return row


# --- lazy creation ----------------------------------------------------------

def test_a_user_who_never_trains_costs_no_knowledge_base():
    c = FakeClient(rows=[user()])

    assert UserKbService(c).kb_ids(user()) == []
    assert c.created_kbs == []


def test_a_short_document_creates_the_whole_document_tier():
    c = FakeClient(rows=[user()])

    kb_id = UserKbService(c).ensure_kb(user(), full_document=True)

    assert kb_id == "kb-1"
    assert c.created_kbs[0]["indexing_config"] == {"strategy": "full_document"}
    assert c.updated_users == [("u1", {"kb_full_id": "kb-1"})]


def test_a_long_document_creates_the_chunked_tier():
    c = FakeClient(rows=[user()])

    UserKbService(c).ensure_kb(user(), full_document=False)

    assert c.created_kbs[0]["indexing_config"] is None
    assert c.updated_users == [("u1", {"kb_id": "kb-1"})]


def test_each_tier_is_created_once():
    c = FakeClient(rows=[user()])
    svc = UserKbService(c)

    first = svc.ensure_kb(user(), full_document=True)
    second = svc.ensure_kb(user(kb_full_id=first), full_document=True)

    assert first == second
    assert len(c.created_kbs) == 1


def test_the_reranker_config_is_applied():
    c = FakeClient(rows=[user()])

    UserKbService(c, {"reranker": {"model": "m"}}).ensure_kb(user(), full_document=False)

    assert c.created_kbs[0]["retrieval_config"] == {"reranker": {"model": "m"}}


# --- reading ----------------------------------------------------------------

def test_kb_ids_returns_both_tiers_when_present():
    row = user(kb_id="chunked", kb_full_id="whole")
    assert UserKbService(FakeClient()).kb_ids(row) == ["chunked", "whole"]


def test_kb_ids_drops_missing_tiers():
    assert UserKbService(FakeClient()).kb_ids(user(kb_id="chunked")) == ["chunked"]
    assert UserKbService(FakeClient()).kb_ids(None) == []


# --- removing a document ----------------------------------------------------

def test_untrain_unlinks_from_whichever_tier_holds_it():
    c = FakeClient(rows=[user(kb_id="chunked", kb_full_id="whole")])
    c.kb_items = {
        "chunked": [{"id": "idx-a", "source_id": "src-1"}],
        "whole": [{"id": "idx-b", "source_id": "src-2"}],
    }

    assert UserKbService(c).untrain(c.rows["u1"], "src-2") is True
    # The unlink takes the indexed-source id, not the source_id.
    assert c.removed == [("whole", "idx-b")]


def test_untrain_reports_a_document_this_user_does_not_have():
    c = FakeClient(rows=[user(kb_id="chunked")])
    c.kb_items = {"chunked": [{"id": "idx-a", "source_id": "src-1"}]}

    assert UserKbService(c).untrain(c.rows["u1"], "someone-elses") is False
    assert c.removed == []
