# Size-Adaptive Indexing — Design Spec

**Date:** 2026-07-31
**Status:** Approved (design), pending implementation plan

## Goal

Route each uploaded document to a knowledge base whose indexing strategy fits
its size, so small documents are returned **whole** on a match (better
completeness) while large documents are chunked (scalable). Small ≤ threshold →
`full_document`; large → `chunk_embed`.

## Why (and the key constraint)

Powabase's **indexing strategy is per-KB**, not per-document (`indexing_config.
strategy`, stored on the KB; changing it needs a reindex). And **`page_index`
retrieval is `tree_search`-only**, which can't share a single retrieval call
with `full_document`'s hybrid. Therefore:

- We use **two KBs per session**: one `full_document`, one `chunk_embed` — both
  **hybrid**-retrievable, so retrieval stays a **single** context-handler call.
- `full_document` "returns the whole doc," but the context-handler still caps
  injected context at `max_context_tokens`. So the budget must be raised for the
  whole-doc benefit to be real, and "small" must stay within a sane token count.

## Decisions (locked during brainstorming)

1. **Small ≤ 128 KB → `full_document`; larger → `chunk_embed`.** Size signal =
   **raw uploaded byte length** (`len(content)`), known at upload. Threshold in
   config: `full_document_max_bytes = 131072`.
2. **Raise retrieval budget:** `retrieval_top_k 4 → 8`, `retrieval_max_context_
   tokens 2000 → 16000` — so a whole small doc actually lands in the prompt, and
   large chunked docs return fuller context too.
3. **Two KBs per session, lazily created.** `sessions.kb_id` = the `chunk_embed`
   KB (unchanged role); new nullable column **`kb_full_id`** = the
   `full_document` KB. Each is created on the first upload that needs it.
4. **One hybrid retrieval** over `[kb_id, kb_full_id, general_kb_id]`, filtered
   for the ones that exist (the general KB stays `chunk_embed`/hybrid).
5. Cost trade-off accepted: `full_document` does one LLM summary per doc at
   index time, and a 16K-token budget costs more per answer than 2K. Worth it
   for completeness.

## Data model

- **Migration `003_add_kb_full_id.sql`** (run once in Studio):
  `alter table public.sessions add column if not exists kb_full_id text;`
  (nullable; existing rows get NULL — they simply have no full-doc KB).

## Backend changes

### Client (`powabase_client.py`)
- `create_knowledge_base(name, description="", indexing_config=None)` — include
  `indexing_config` in the POST body only when provided (e.g.
  `{"strategy": "full_document"}`). `chunk_embed` is the platform default, so the
  chunk KB passes `indexing_config=None`.

### Config (`core/config.py`)
- Add `full_document_max_bytes: int = 131072`.
- Change `retrieval_top_k` default `4 → 8`, `retrieval_max_context_tokens`
  `2000 → 16000`.

### Session service (`services/session_service.py`)
- Replace `ensure_kb(row)` with `ensure_kb(row, full_document: bool) -> str`:
  - column = `"kb_full_id"` if `full_document` else `"kb_id"`.
  - if the row already has that column set → return it.
  - else create `session-<id>-full` (with `indexing_config={"strategy":
    "full_document"}`) or `session-<id>-kb` (chunk, `indexing_config=None`);
    persist the id via `update_session(session_id, {column: kb_id})`; return it.
- `create_session` unchanged (still inserts `kb_id: ""`, agent created; it does
  **not** reference `kb_full_id`, so a not-yet-migrated DB still creates
  sessions — only the small-doc upload path needs the column).

### Ingest route (`api/routes/ingest.py`)
- After ownership check: `full = len(content) <= settings.full_document_max_
  bytes`; `kb_id = ensure_kb(row, full)`; ingest into that KB.

### Chat route (`api/routes/chat.py`)
- Retrieval list becomes `[row["kb_id"], row.get("kb_full_id"), general_kb_id]`
  (ChatService already drops falsy ids). No ChatService change — it still takes
  `retrieval_kb_ids`, `top_k`, `max_context_tokens`; the raised config values
  flow through.

## Behavior notes

- A session with only large docs → just the chunk KB (as today). Only small docs
  → just the full-doc KB. Mixed → both, searched together.
- Chatting before any upload still works (retrieves the general KB only).
- Existing sessions keep their `kb_id`; `kb_full_id` is NULL until a small doc is
  uploaded — fully backward-compatible.
- The two KBs are both created lazily; `session_service.delete` already
  best-effort-deletes `kb_id` and must also delete `kb_full_id`.

## Testing

- **Unit:** client `create_knowledge_base` sends `indexing_config` when set (and
  omits it when None); config new/changed defaults; `session_service.ensure_kb`
  for both branches (creates the right-named KB with the right `indexing_config`,
  persists to the right column, returns existing without recreating);
  `session_service.delete` also removes `kb_full_id`; ingest route routes a small
  vs large upload to the correct KB (via a fake that records the `full` flag);
  chat route builds the 3-KB retrieval list (falsy filtered).
- **Live smoke:** upload a small (<128 KB) doc → a `session-<id>-full` KB is
  created with `full_document` strategy; upload a large (>128 KB) doc → a
  `session-<id>-kb` chunk KB; a question answerable only from the small doc
  returns a complete, cited answer; **verify a single retrieval call spans both
  KBs** (Powabase mixes full_document + chunk_embed under hybrid) — if it does
  not, fall back to two calls (out of scope unless observed). Confirm
  `max_context_tokens=16000` is honored (small doc returned substantially whole).

## Non-goals

- No `page_index`/`tree_search` (retrieval-incompatible with full_document in one
  call — explicitly rejected).
- No per-page or per-token size signal (byte length only).
- No reranker/query-enrichment (separate future tuning).
- No change to auth/ownership, the gate, dedup, or the general-knowledge flow.
