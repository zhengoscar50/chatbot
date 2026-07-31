import pytest

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
