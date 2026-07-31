# Async Ingestion with Status Polling — Design Spec

**Date:** 2026-07-31
**Status:** Approved (design), pending implementation plan

## Goal

Make document ingestion robust to slow/large files. Uploads return immediately;
the extract → add-to-KB → index pipeline finishes in the background; the
frontend polls a status endpoint and reflects progress. This fixes a real bug
and makes large docs usable end-to-end.

## The bug this fixes

`IngestService.ingest_pdf` runs `upload → wait_for_extraction → add_source_to_kb
→ wait_for_indexing` synchronously with a 60s cap. When **extraction** exceeds
the cap, it raises `IngestTimeout` and returns 202 **before `add_source_to_kb`
is ever called** — so the source is uploaded and (eventually) extracted but
**never added to the KB, never indexed**: orphaned and unusable for that
session. Observed live with a ~195 KB PDF. Raising the wait only moves the
cliff and makes the request hang for minutes, so we go async instead.

## Decisions (locked during brainstorming)

1. **Background finish + status polling** (not "raise the wait").
2. **Chatting stays allowed** while a doc indexes — the attachment chip shows
   progress; asking before it's ready simply misses the doc (re-ask later).
3. Statuses exposed to the UI: **`processing` | `indexed` | `failed`** (+ an
   optional `detail`, e.g. needs-OCR).

## Backend

### IngestService (split the pipeline)
- `start(filename, content) -> str` — just `upload_source` (handles the 409
  dedup); returns `source_id`. Fast, runs in-request.
- `finish(source_id) -> None` — `_wait_for_extraction` → `add_source_to_kb(self.
  kb_id, source_id)` → `_wait_for_indexing`, using a **generous** `max_wait`.
  Raises the existing typed errors; the background wrapper swallows them (the
  status endpoint is the source of truth).
- Keep `ingest_pdf` as `start` + `finish` for any remaining synchronous use, or
  remove it if unused. (Existing tests updated accordingly.)

### `POST /ingest/file` (now non-blocking)
- Ownership 404 → `full_document = len(content) <= full_document_max_bytes` →
  `kb_id = ensure_kb(row, full_document)` → `source_id = service.start(...)`
  (502 on `PowabaseAPIError`) → schedule `finish` via **`BackgroundTasks`** →
  return **202 `{source_id, status: "processing"}`**.
- The background task wraps `service.finish(source_id)` in a try/except that
  swallows `AttentionRequiredError` / `ExtractionFailedError` /
  `IndexingFailedError` / `IngestTimeoutError` / `PowabaseAPIError` (failures are
  observable via the status endpoint).

### `GET /ingest/status/{source_id}?session_id=…` (new, owner-gated)
- Ownership 404 (same as chat/ingest). Resolves status from `get_source` + the
  session's KB(s) (`kb_id` and `kb_full_id`):
  - `extraction_status` in `pending`/`extracting` → **processing**.
  - `attention_required` → **failed**, detail "needs OCR re-extraction".
  - `failed`/`cancelled` → **failed**.
  - `extracted` but not yet found in either KB's sources → **processing** (mid-
    pipeline, between extract and add).
  - found with `index_status` `indexed` → **indexed**; `pending`/`indexing` →
    **processing**; `failed`/`cancelled` → **failed**.
- Returns `IngestStatusResponse{source_id, status, detail: Optional[str]}`.

### Config
- `ingest_background_max_wait_seconds: int = 600` — the background `finish` limit
  (separate from the short foreground `ingest_max_wait_seconds`, which is now
  only relevant if any synchronous path remains).

### Schemas
- `IngestResponse` stays `{source_id, status}` (status is now `"processing"` on
  the 202). Add `IngestStatusResponse{source_id: str, status: str, detail:
  Optional[str] = None}`.

## Frontend (`app.js`)

- Upload → on **202**, show the chip as **"Indexing…"** and start polling
  `GET /ingest/status/{source_id}?session_id=…` (via `authFetch`) every ~3s:
  - `indexed` → chip "indexed" (ok state), stop.
  - `failed` → chip shows the detail (error state), stop.
  - `processing` → keep polling; give up after ~10 min with "still processing —
    check back", stop.
- The composer stays enabled throughout. Track the poll by `source_id`; starting
  a new upload or switching sessions cancels the previous poll (single active
  chip, as today).

## Known limitation (documented, not built)

`BackgroundTasks` run **in-process**: a server restart mid-index loses that task
(the doc would sit `processing` until re-uploaded). Acceptable for this single-
process demo; a production version would use a durable task queue (Celery/RQ).

## Testing

- **Unit:** `IngestService.start` (upload only, dedup) and `finish` (extract →
  add → index; typed errors) as separate units; `POST /ingest/file` returns 202
  with `status:"processing"` and enqueues exactly one background task (ownership
  404 still checked first, before upload/enqueue); the background wrapper
  swallows the typed ingest errors; the status endpoint maps every `get_source`/
  KB-index state → processing/indexed/failed (+ needs-OCR detail) and is
  owner-gated (404 for a non-owner or unknown session).
- **Live smoke:** upload a **large** PDF → immediate 202; poll `/ingest/status`
  until `indexed` (proving the orphan bug is gone — it completes); then a
  question about that doc returns a cited answer. Upload a broken/tiny non-PDF →
  status eventually `failed` with a detail.

## Non-goals

- No durable task queue / cross-restart recovery.
- No per-document progress percentage (coarse processing/indexed/failed only).
- No blocking of chat during indexing.
- No change to retrieval, auth/ownership, the gate, or size routing.
