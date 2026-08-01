# Content-Aware Indexing Routing — Design Spec

**Date:** 2026-08-01
**Status:** Approved (design), pending implementation plan

## Goal

Route documents to `full_document` vs `chunk_embed` by their **extracted text
size** (character count) instead of raw file bytes, so a document whose text
fits the context window is returned **whole** (great for "summarize the whole
doc" questions) while genuinely large documents are chunked. Fixes the reported
case: a scanned `constitution.pdf` (byte-huge, ~13K tokens of text) was
mis-routed to chunking, so holistic summaries came back partial.

## Evidence (diagnosed live)

- The constitution source's `auto_metadata` reports `char_count: 52900`,
  `page_count: 19`, `extraction_method: "lighton_ocr"` — a **scanned PDF**:
  byte-large (19 page images) but text-small.
- Indexing that same source as `full_document` and retrieving returned the
  **entire text** (53,160 chars; contains Preamble, Articles I/II/III, Bill of
  Rights, amendments) — the model would summarize everything.
- `get_source(...)["auto_metadata"]["char_count"]` is the correct, available
  routing signal (populated after extraction).

## Decisions

1. **Route by extracted `char_count`, not file bytes.** `full_document` if
   `0 < char_count <= full_document_max_chars`, else `chunk_embed`. An
   unknown/zero char_count → `chunk_embed` (safe for large/uncertain docs).
2. **Threshold `full_document_max_chars = 120000`** (~30K tokens), configurable.
3. **Bump `retrieval_max_context_tokens` 16000 → 32000** so a whole
   fits-the-window doc (up to ~30K tokens) actually lands in the prompt.
4. The routing decision **moves into the async background step** (`finish`),
   which already runs after extraction — the only place `char_count` is known.
   The upload route no longer chooses the KB.
5. **Backfill:** re-route the user's existing `constitution.pdf` into a
   `full_document` KB (proven fix) as part of rollout. General re-routing of all
   previously-uploaded docs is out of scope.
6. `full_document_max_bytes` is removed (byte routing is gone).

## Backend changes

### Config (`core/config.py`)
- Remove `full_document_max_bytes`.
- Add `full_document_max_chars: int = 120000`.
- Change `retrieval_max_context_tokens` default `16000 → 32000`.

### IngestService (`services/ingest_service.py`) — expose the phases
Keep `start`, `finish`, `ingest_pdf` (admin general-knowledge training stays
synchronous and KB-bound). Add:
- `await_extraction(source_id) -> None` — public wrapper of the extraction wait
  (raises the existing typed errors).
- `char_count(source_id) -> int` — `get_source(...).get("auto_metadata",{}).get(
  "char_count") or 0`.
- `index_into(kb_id, source_id) -> str` — `add_source_to_kb(kb_id, source_id)`
  then wait-indexing; returns the final status.
(`finish` = `await_extraction` + `index_into(self.kb_id)`.)

### Ingest route (`api/routes/ingest.py`) — decide the KB post-extraction
- `POST /ingest/file`: ownership 404 → `service.start(...)` (502 on
  `PowabaseAPIError`) → schedule a background task with `(service, sessions, row,
  source_id, full_document_max_chars)` → **202 `{source_id, status:"processing"}`**.
  No `ensure_kb` / byte routing at request time.
- Background `_run_finish(service, sessions, row, source_id, max_chars)`:
  `await_extraction` → `full_document = 0 < service.char_count(source_id) <=
  max_chars` → `kb_id = sessions.ensure_kb(row, full_document)` → `index_into(
  kb_id, source_id)`; swallow the typed ingest errors + `PowabaseAPIError`
  (observable via `/ingest/status`).
- The status endpoint is unchanged (already checks both session KBs; a source
  mid-pipeline / not-yet-in-a-KB reports `processing`).

## Backfill + verification (one-time, at rollout)

- Move the existing `constitution.pdf` source into its session's `full_document`
  KB: ensure the session's full KB exists (create with the `full_document`
  strategy + reranker, persist `kb_full_id`), `add_source_to_kb(full_kb, source)`,
  wait indexed; optionally remove it from the chunk KB.

## Testing

- **Unit:** config (`full_document_max_chars` default; `retrieval_max_context_
  tokens == 32000`; `full_document_max_bytes` gone); `IngestService.await_
  extraction`/`char_count`/`index_into`; the route returns 202 + schedules the
  background task (ownership 404 first, no KB chosen at request time); the
  background task routes `full_document=True` for a small `char_count` and
  `False` for a large one (fake client drives `char_count`; fake session service
  records the flag); unknown char_count → chunk.
- **Live smoke:** re-route the existing constitution → `full_document`; a
  "summarize the main articles" retrieval returns the whole text (all Articles +
  Bill of Rights). Upload a small text doc → `full_document` KB; upload a
  >120K-char text doc → `chunk_embed` KB. A scanned/byte-large-but-text-small doc
  → `full_document` (the fix). Cited answers throughout.

## Non-goals

- No general re-routing of all previously-uploaded documents (just the reported
  constitution).
- No change to reranking, the gate, ownership, or the async status endpoint.
- No page-count or token-exact signal (character count is the proxy).
