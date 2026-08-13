import pytest

from app.clients.powabase_client import PowabaseAPIError

from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
    source_status,
)


class FakeClient:
    def __init__(self, source_statuses, index_statuses):
        self.source_statuses = list(source_statuses)
        self.index_statuses = list(index_statuses)
        self.added_to_kb = []
        self.listed_kbs = []

    def upload_source(self, filename, content):
        return {"id": "src-1"}

    def get_source(self, source_id):
        status = (
            self.source_statuses.pop(0)
            if len(self.source_statuses) > 1
            else self.source_statuses[0]
        )
        return {"extraction_status": status, "error_message": "boom"}

    def add_source_to_kb(self, kb_id, source_id):
        self.added_to_kb.append((kb_id, source_id))
        return {"id": "indexed-1"}

    def list_kb_sources(self, kb_id):
        self.listed_kbs.append(kb_id)
        status = (
            self.index_statuses.pop(0)
            if len(self.index_statuses) > 1
            else self.index_statuses[0]
        )
        return {
            "items": [
                {"source_id": "src-1", "index_status": status, "error_message": "boom"}
            ]
        }


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.services.ingest_service.time.sleep", lambda seconds: None)


def test_ingest_pdf_success_path():
    client = FakeClient(
        source_statuses=["extracting", "extracted"],
        index_statuses=["indexing", "indexed"],
    )
    service = IngestService(client, kb_id="kb-1", poll_interval=0.01, max_wait=5.0)

    result = service.ingest_pdf("doc.pdf", b"bytes")

    assert result == {"source_id": "src-1", "status": "indexed"}
    assert client.added_to_kb == [("kb-1", "src-1")]


def test_ingest_pdf_raises_on_attention_required():
    client = FakeClient(source_statuses=["attention_required"], index_statuses=["indexed"])
    service = IngestService(client, kb_id="kb-1", poll_interval=0.01, max_wait=5.0)

    with pytest.raises(AttentionRequiredError) as exc_info:
        service.ingest_pdf("doc.pdf", b"bytes")

    assert exc_info.value.source_id == "src-1"


def test_ingest_pdf_raises_on_extraction_failed():
    client = FakeClient(source_statuses=["failed"], index_statuses=["indexed"])
    service = IngestService(client, kb_id="kb-1", poll_interval=0.01, max_wait=5.0)

    with pytest.raises(ExtractionFailedError):
        service.ingest_pdf("doc.pdf", b"bytes")


def test_ingest_pdf_raises_on_indexing_failed():
    client = FakeClient(source_statuses=["extracted"], index_statuses=["failed"])
    service = IngestService(client, kb_id="kb-1", poll_interval=0.01, max_wait=5.0)

    with pytest.raises(IndexingFailedError):
        service.ingest_pdf("doc.pdf", b"bytes")


def test_ingest_pdf_raises_timeout_when_extraction_never_terminates():
    client = FakeClient(source_statuses=["extracting"], index_statuses=["indexed"])
    service = IngestService(client, kb_id="kb-1", poll_interval=0.01, max_wait=0)

    with pytest.raises(IngestTimeoutError) as exc_info:
        service.ingest_pdf("doc.pdf", b"bytes")

    assert exc_info.value.status == "extracting"


def test_start_uploads_and_returns_source_id():
    client = FakeClient(source_statuses=["extracted"], index_statuses=["indexed"])
    service = IngestService(client, kb_id="kb-1", poll_interval=0, max_wait=1)
    source_id = service.start("doc.pdf", b"bytes")
    assert source_id == "src-1"


def test_finish_runs_extract_add_index():
    client = FakeClient(source_statuses=["extracted"], index_statuses=["indexed"])
    service = IngestService(client, kb_id="kb-1", poll_interval=0, max_wait=1)
    sid = service.start("doc.pdf", b"bytes")
    result = service.finish(sid)
    assert result == "indexed"
    assert client.added_to_kb == [("kb-1", "src-1")]


def test_source_status_processing_indexed_failed():
    # extraction still going
    class C1:
        def get_source(self, s):
            return {"extraction_status": "extracting"}

    assert source_status(C1(), "s", ["kb-1"]) == ("processing", None)

    # needs OCR
    class C2:
        def get_source(self, s):
            return {"extraction_status": "attention_required"}

    assert source_status(C2(), "s", ["kb-1"])[0] == "failed"

    # extracted + indexed in the KB
    class C3:
        def get_source(self, s):
            return {"extraction_status": "extracted"}

        def list_kb_sources(self, kb):
            return {"items": [{"source_id": "s", "index_status": "indexed"}]}

    assert source_status(C3(), "s", ["kb-1"]) == ("indexed", None)

    # extracted but not yet added to any KB
    class C4:
        def get_source(self, s):
            return {"extraction_status": "extracted"}

        def list_kb_sources(self, kb):
            return {"items": []}

    assert source_status(C4(), "s", ["kb-1", ""]) == ("processing", None)

    # indexing failed
    class C5:
        def get_source(self, s):
            return {"extraction_status": "extracted"}

        def list_kb_sources(self, kb):
            return {"items": [{"source_id": "s", "index_status": "failed"}]}

    assert source_status(C5(), "s", ["kb-1"])[0] == "failed"


def test_char_count_reads_auto_metadata():
    class C:
        def get_source(self, s):
            return {"auto_metadata": {"char_count": 4200}}

    assert IngestService(C(), None, poll_interval=0, max_wait=1).char_count("s") == 4200


def test_char_count_zero_when_missing():
    class C:
        def get_source(self, s):
            return {}

    assert IngestService(C(), None, poll_interval=0, max_wait=1).char_count("s") == 0


def test_index_into_adds_then_waits():
    client = FakeClient(["extracted"], ["indexed"])
    svc = IngestService(client, None, poll_interval=0, max_wait=1)
    assert svc.index_into("kb-9", "src-1") == "indexed"
    assert client.added_to_kb == [("kb-9", "src-1")]
    assert "kb-9" in client.listed_kbs


def test_await_extraction_ok_and_finish_still_works():
    client = FakeClient(["extracted"], ["indexed"])
    svc = IngestService(client, "kb-1", poll_interval=0, max_wait=1)
    svc.await_extraction("src-1")  # no raise
    assert svc.finish("src-1") == "indexed"  # admin path intact


# --- transient upstream failures while polling ------------------------------

class BlippingClient:
    """Upstream fails for the first `blips` polls, then behaves.

    Powabase's gateway intermittently 502s while it is busy extracting a large
    document — exactly when polling runs longest.
    """

    def __init__(self, blips, final="extracted", index_final="indexed"):
        self.blips = blips
        self.final = final
        self.index_final = index_final
        self.source_polls = 0
        self.index_polls = 0

    def get_source(self, source_id):
        self.source_polls += 1
        if self.source_polls <= self.blips:
            raise PowabaseAPIError(502, "<html>502 Bad Gateway</html>")
        return {"extraction_status": self.final, "error_message": ""}

    def add_source_to_kb(self, kb_id, source_id):
        return {"id": "indexed-1"}

    def list_kb_sources(self, kb_id):
        self.index_polls += 1
        if self.index_polls <= self.blips:
            raise PowabaseAPIError(502, "<html>502 Bad Gateway</html>")
        return {"items": [{"source_id": "src-1", "index_status": self.index_final}]}


def test_extraction_polling_survives_a_transient_upstream_failure():
    """A one-second gateway wobble during a four-minute extraction must not
    abort the whole ingest. This is the 502 users actually hit."""
    client = BlippingClient(blips=2)
    service = IngestService(client, "kb-1", poll_interval=0, max_wait=5)

    service.await_extraction("src-1")   # must not raise

    assert client.source_polls == 3     # two failures, then success


def test_indexing_polling_survives_a_transient_upstream_failure():
    client = BlippingClient(blips=2)
    service = IngestService(client, "kb-1", poll_interval=0, max_wait=5)

    assert service.index_into("kb-1", "src-1") == "indexed"


def test_polling_still_gives_up_when_upstream_stays_broken():
    """Tolerance is not infinite patience: a genuinely dead upstream must still
    surface rather than spin until the deadline with no explanation."""
    client = BlippingClient(blips=10_000)
    service = IngestService(client, "kb-1", poll_interval=0, max_wait=5)

    with pytest.raises(PowabaseAPIError):
        service.await_extraction("src-1")
