import time


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
    def __init__(self, client, kb_id: str, poll_interval: float = 2.0, max_wait: float = 60.0):
        self.client = client
        self.kb_id = kb_id
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    def ingest_pdf(self, filename: str, content: bytes) -> dict:
        source = self.client.upload_source(filename, content)
        source_id = source["id"]
        self._wait_for_extraction(source_id)
        self.client.add_source_to_kb(self.kb_id, source_id)
        status = self._wait_for_indexing(source_id)
        return {"source_id": source_id, "status": status}

    def _wait_for_extraction(self, source_id: str) -> None:
        deadline = time.monotonic() + self.max_wait
        while True:
            source = self.client.get_source(source_id)
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

    def _wait_for_indexing(self, source_id: str) -> str:
        deadline = time.monotonic() + self.max_wait
        while True:
            sources = self.client.list_kb_sources(self.kb_id)
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
