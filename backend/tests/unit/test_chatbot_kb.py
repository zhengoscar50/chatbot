import pytest

from app.clients.powabase_client import PowabaseAPIError
from app.services.chatbot_kb import ChatbotKbService


class FakeClient:
    def __init__(self, rows=None):
        self.rows = {r["id"]: r for r in (rows or [])}
        self.created_kbs = []
        self.updated_chatbots = []
        self.kb_items = {}
        self.removed = []

    def get_chatbot_row(self, chatbot_id):
        return self.rows.get(chatbot_id)

    def update_chatbot_row(self, chatbot_id, fields):
        self.updated_chatbots.append((chatbot_id, fields))
        self.rows[chatbot_id].update(fields)

    def create_knowledge_base(self, name, description="", indexing_config=None,
                              retrieval_config=None):
        kb = {"id": "kb-%d" % (len(self.created_kbs) + 1), "name": name,
              "indexing_config": indexing_config,
              "retrieval_config": retrieval_config}
        self.created_kbs.append(kb)
        return kb

    def list_kb_sources(self, kb_id):
        return {"items": self.kb_items.get(kb_id, [])}

    def remove_source_from_kb(self, kb_id, indexed_source_id):
        self.removed.append((kb_id, indexed_source_id))


def bot(**over):
    return dict({"id": "cb-1", "kb_id": None, "kb_full_id": None}, **over)


def test_untrained_chatbot_has_no_kbs():
    assert ChatbotKbService(FakeClient()).kb_ids(bot()) == []


def test_kb_ids_are_chunked_then_full():
    row = bot(kb_id="chunk", kb_full_id="full")
    assert ChatbotKbService(FakeClient()).kb_ids(row) == ["chunk", "full"]


def test_kb_ids_of_a_missing_chatbot_is_empty():
    assert ChatbotKbService(FakeClient()).kb_ids(None) == []


def test_ensure_kb_creates_the_chunked_tier_lazily_and_records_it():
    row = bot()
    client = FakeClient([row])
    kb_id = ChatbotKbService(client).ensure_kb(row, full_document=False)
    assert kb_id == "kb-1"
    assert client.updated_chatbots == [("cb-1", {"kb_id": "kb-1"})]
    # The chunked tier takes Powabase's default strategy.
    assert client.created_kbs[0]["indexing_config"] is None


def test_ensure_kb_creates_the_full_document_tier_with_its_strategy():
    row = bot()
    client = FakeClient([row])
    kb_id = ChatbotKbService(client).ensure_kb(row, full_document=True)
    assert client.updated_chatbots == [("cb-1", {"kb_full_id": "kb-1"})]
    assert client.created_kbs[0]["indexing_config"] == {"strategy": "full_document"}


def test_ensure_kb_reuses_an_existing_tier():
    row = bot(kb_id="already")
    client = FakeClient([row])
    assert ChatbotKbService(client).ensure_kb(row) == "already"
    assert client.created_kbs == []


def test_two_chatbots_get_separate_knowledge_bases():
    # The whole point of phase 2. Same owner, different documents.
    a, b = bot(id="cb-a"), bot(id="cb-b")
    client = FakeClient([a, b])
    service = ChatbotKbService(client)
    assert service.ensure_kb(a) != service.ensure_kb(b)


def test_documents_spans_both_tiers():
    row = bot(kb_id="chunk", kb_full_id="full")
    client = FakeClient([row])
    client.kb_items["chunk"] = [
        {"id": "i1", "source_id": "s1", "source_name": "a.pdf", "index_status": "indexed"}
    ]
    client.kb_items["full"] = [
        {"id": "i2", "source_id": "s2", "source_name": "b.pdf", "index_status": "indexed"}
    ]
    docs = ChatbotKbService(client).documents(row)
    assert [d["source_id"] for d in docs] == ["s1", "s2"]
    assert docs[0]["filename"] == "a.pdf"


def test_untrain_unlinks_by_indexed_id_and_never_deletes_the_source():
    # upload_source deduplicates identical content, so the same Source may
    # belong to another chatbot or another user. Only the LINK may go.
    row = bot(kb_id="chunk")
    client = FakeClient([row])
    client.kb_items["chunk"] = [{"id": "i1", "source_id": "s1"}]
    assert ChatbotKbService(client).untrain(row, "s1") is True
    assert client.removed == [("chunk", "i1")]


def test_untrain_reports_a_document_it_does_not_hold():
    row = bot(kb_id="chunk")
    client = FakeClient([row])
    client.kb_items["chunk"] = []
    assert ChatbotKbService(client).untrain(row, "nope") is False


def test_contains_finds_a_document_already_indexed():
    client = FakeClient()
    client.kb_items["chunk"] = [{"id": "i1", "source_id": "s1"}]
    assert ChatbotKbService(client).contains("chunk", "s1") is True


def test_contains_is_false_for_a_document_not_there():
    client = FakeClient()
    client.kb_items["chunk"] = [{"id": "i1", "source_id": "s1"}]
    assert ChatbotKbService(client).contains("chunk", "other") is False
