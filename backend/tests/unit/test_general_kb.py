from app.services.general_kb import GENERAL_KB_NAME, ensure_general_kb


class FakeClient:
    def __init__(self, existing=None):
        self.kbs = list(existing or [])
        self.created = []

    def list_knowledge_bases(self):
        return {"knowledge_bases": self.kbs}

    def create_knowledge_base(self, name, description=""):
        kb = {"id": f"kb-{name}", "name": name}
        self.kbs.append(kb)
        self.created.append(kb)
        return kb


def test_ensure_general_kb_creates_when_absent():
    client = FakeClient()
    kb_id = ensure_general_kb(client)
    assert kb_id == f"kb-{GENERAL_KB_NAME}"
    assert client.created and client.created[0]["name"] == GENERAL_KB_NAME


def test_ensure_general_kb_reuses_when_present():
    client = FakeClient(existing=[{"id": "kb-existing", "name": GENERAL_KB_NAME}])
    kb_id = ensure_general_kb(client)
    assert kb_id == "kb-existing"
    assert client.created == []
