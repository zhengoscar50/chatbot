import time

from app.clients.powabase_client import PowabaseAPIError


class AttentionRequiredError(Exception):
    def __init__(self, source_id: str):
        self.source_id = source_id
        super().__init__(f"Source {source_id} needs OCR re-extraction")


class ExtractionFailedError(Exception):
    def __init__(self, source_id: str, message: str):
        self.source_id = source_id
        self.message = message
        super().__init__(message)


class IndexingFailedError(Exception):
    def __init__(self, source_id: str, message: str):
        self.source_id = source_id
        self.message = message
        super().__init__(message)


class IngestTimeoutError(Exception):
    def __init__(self, source_id: str, status: str):
        self.source_id = source_id
        self.status = status
        super().__init__(f"Source {source_id} still {status} after max wait")


class IngestService:
    def __init__(self, client, kb_id: str = None, poll_interval: float = 2.0, max_wait: float = 60.0):
        self.client = client
        self.kb_id = kb_id
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    def start(self, filename: str, content: bytes) -> str:
        return self.client.upload_source(filename, content)["id"]

    def await_extraction(self, source_id: str) -> None:
        self._wait_for_extraction(source_id)

    def char_count(self, source_id: str) -> int:
        return self.client.get_source(source_id).get("auto_metadata", {}).get("char_count") or 0

    def index_into(self, kb_id: str, source_id: str) -> str:
        self.client.add_source_to_kb(kb_id, source_id)
        return self._wait_for_indexing(kb_id, source_id)

    def finish(self, source_id: str) -> str:
        self.await_extraction(source_id)
        return self.index_into(self.kb_id, source_id)

    def ingest_pdf(self, filename: str, content: bytes) -> dict:
        source_id = self.start(filename, content)
        return {"source_id": source_id, "status": self.finish(source_id)}

    # Powabase's gateway intermittently returns 502/503 while it is busy
    # processing a large document — precisely when polling runs longest. One
    # such blip used to abort a whole ingest. Tolerate a few in a row, but not
    # indefinitely: a genuinely dead upstream must still surface.
    TRANSIENT_UPSTREAM = frozenset({429, 500, 502, 503, 504})
    MAX_CONSECUTIVE_UPSTREAM_FAILURES = 5

    def _poll(self, call, failures: int):
        """Run a polling call, returning (result, failures) or (None, failures).

        Returns None for a tolerated transient failure, so the caller sleeps
        and tries again. Re-raises once the run of failures is too long, or if
        the error is not the transient kind.
        """
        try:
            return call(), 0
        except PowabaseAPIError as e:
            failures += 1
            if (e.status_code not in self.TRANSIENT_UPSTREAM
                    or failures > self.MAX_CONSECUTIVE_UPSTREAM_FAILURES):
                raise
            return None, failures

    def _wait_for_extraction(self, source_id: str) -> None:
        deadline = time.monotonic() + self.max_wait
        failures = 0
        while True:
            source, failures = self._poll(
                lambda: self.client.get_source(source_id), failures
            )
            if source is None:
                if time.monotonic() >= deadline:
                    raise IngestTimeoutError(source_id, "upstream unavailable")
                time.sleep(self.poll_interval)
                continue
            status = source["extraction_status"]
            if status == "extracted":
                return
            if status == "attention_required":
                raise AttentionRequiredError(source_id)
            if status in ("failed", "cancelled"):
                raise ExtractionFailedError(source_id, source.get("error_message", status))
            if time.monotonic() >= deadline:
                raise IngestTimeoutError(source_id, status)
            time.sleep(self.poll_interval)

    def _wait_for_indexing(self, kb_id: str, source_id: str) -> str:
        deadline = time.monotonic() + self.max_wait
        failures = 0
        while True:
            sources, failures = self._poll(
                lambda: self.client.list_kb_sources(kb_id), failures
            )
            if sources is None:
                if time.monotonic() >= deadline:
                    raise IngestTimeoutError(source_id, "upstream unavailable")
                time.sleep(self.poll_interval)
                continue
            entry = next(
                (item for item in sources["items"] if item.get("source_id") == source_id),
                None,
            )
            if entry is None:
                if time.monotonic() >= deadline:
                    raise IngestTimeoutError(source_id, "pending")
                time.sleep(self.poll_interval)
                continue
            status = entry["index_status"]
            if status == "indexed":
                return status
            if status in ("failed", "cancelled"):
                raise IndexingFailedError(source_id, entry.get("error_message", status))
            if time.monotonic() >= deadline:
                raise IngestTimeoutError(source_id, status)
            time.sleep(self.poll_interval)


def source_status(client, source_id: str, kb_ids) -> tuple:
    """Coarse ingest status for a source: processing | indexed | failed."""
    ext = client.get_source(source_id).get("extraction_status")
    if ext == "attention_required":
        return "failed", "Needs OCR re-extraction (low-quality/scanned PDF)."
    if ext in ("failed", "cancelled"):
        return "failed", "Extraction failed."
    if ext != "extracted":
        return "processing", None  # pending / extracting / unknown
    for kb_id in kb_ids:
        if not kb_id:
            continue
        items = client.list_kb_sources(kb_id).get("items", [])
        entry = next((i for i in items if i.get("source_id") == source_id), None)
        if entry is None:
            continue
        idx = entry.get("index_status")
        if idx == "indexed":
            return "indexed", None
        if idx in ("failed", "cancelled"):
            return "failed", "Indexing failed."
        return "processing", None  # pending / indexing
    return "processing", None  # extracted but not added to a KB yet
