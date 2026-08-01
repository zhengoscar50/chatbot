# Retrieval Reranking — Design Spec

**Date:** 2026-08-01
**Status:** Approved (design), pending implementation plan

## Goal

Add a cross-encoder **reranker** to every knowledge base's retrieval config so
low-content chunks (bare section headers, repeating page running-headers) are
demoted below real body text. Fixes retrieval returning "only headers and page
numbers" on broad/summary questions.

## Evidence (diagnosed live)

On a real `constitution.pdf` KB, a "summarize the articles" query returned, top-6:
- **hybrid (current):** 4 header-only junk chunks (`# Article. V.` +
  `CONSTITUTION OF THE UNITED STATES`, ~47 chars) on top; real text last.
- **vector_search only:** identical junk (switching method does nothing).
- **hybrid + reranker (`cohere/rerank-english-v3.0`):** real article text
  dominates (Article V/VI/VII bodies, the President clause); one header chunk
  left, demoted.

The reranker **actively reordered** (didn't fail open), confirming a reranker
key is configured on the project. Reranking alone fixes the symptom — the
originally-considered content-size routing change is **not needed**.

## Decisions

1. Apply a reranker via **`retrieval_config.reranker`** on **every KB the app
   creates**: session `chunk_embed` KBs, session `full_document` KBs, and the
   shared general KB. Query-time, **no re-index**.
2. Default reranker `cohere/rerank-english-v3.0`, `candidate_count = 20` (stage-1
   pool; final count is the per-request `top_k`, already 8). Configurable.
3. **Backfill existing KBs** (incl. the user's current documents) so they benefit
   without re-upload — a one-time PATCH of each KB's `retrieval_config`.
4. **Graceful/portable:** if the reranker model setting is empty, pass no
   reranker (so a project without a reranker key just runs plain hybrid). Powabase
   also fails-open if a configured reranker errors.

## Backend changes

### Config (`core/config.py`)
- `reranker_model: str = "cohere/rerank-english-v3.0"` (empty string → disabled).
- `reranker_candidate_count: int = 20`.

### Retrieval-config helper
- A small function `reranker_retrieval_config(model, candidate_count) -> dict |
  None`: returns `{"reranker": {"model": model, "candidate_count":
  candidate_count}}` when `model` is truthy, else `None`. (Merged over the KB's
  retrieval defaults at create time, so `method: hybrid` etc. are preserved.)

### Client (`clients/powabase_client.py`)
- `create_knowledge_base(name, description="", indexing_config=None,
  retrieval_config=None)` — include `retrieval_config` in the POST body only when
  provided.
- `update_knowledge_base(kb_id, fields) -> dict` — `PATCH /api/knowledge-bases/
  {id}` with the given fields (for backfill).

### Wiring
- `main.py` builds the reranker config from settings once at startup and passes
  it to `ensure_general_kb(client, reranker_config)` and to
  `SessionService(client, model, general_kb_id, reranker_config)`.
- `SessionService.__init__` stores `reranker_config`; `ensure_kb` passes it as
  `retrieval_config` on both the chunk and full-document KB creates.
- `general_kb.ensure_general_kb(client, reranker_config=None)` passes it on the
  general KB create.

### Backfill (one-time operation, run at rollout — not app boot)
- A script/step that lists all `session-*` KBs + the general KB, reads each
  one's current `retrieval_config` (read-modify-write to avoid clobbering
  `method`/`context_mode`/`ts_language`), adds the reranker, and PATCHes it back.

## Testing

- **Unit:** `create_knowledge_base` sends `retrieval_config` when set / omits when
  None; `update_knowledge_base` PATCHes the right path/body; `reranker_retrieval_
  config` returns the dict when a model is set and `None` when empty;
  `ensure_kb`/`ensure_general_kb` pass the reranker config through; `main` lifespan
  threads it in.
- **Live smoke:** re-run the constitution "summarize the articles" query after
  wiring + backfill → real article body text in the top results (not header
  chunks); a normal doc question still returns cited answers.

## Non-goals

- No content-size routing change (reranking made it unnecessary).
- No re-indexing (reranking is query-time).
- No new reranker key provisioning (the project already has one; if it ever
  lacks one, the empty-model setting disables reranking gracefully).
- No change to the gate, ownership, async ingest, or size routing.
