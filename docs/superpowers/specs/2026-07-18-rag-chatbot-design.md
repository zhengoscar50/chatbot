# RAG Chatbot on Powabase — Design

Date: 2026-07-18

## Purpose

Build a RAG chatbot backed by [Powabase](https://powabase.ai), a multi-tenant
AI Backend-as-a-Service that natively provides document ingestion/extraction,
Knowledge Bases (chunking, embedding, retrieval), and agents (LLM + tools +
sessions), on top of a Supabase-style BaaS layer (Postgres, PostgREST, auth,
storage).

This supersedes an earlier plan that hand-rolled ingestion/retrieval/chat
against raw Supabase + pgvector + LangChain (see the tutorial doc at
`~/Downloads/Simple RAG Chatbot using Supabase and FastAPI/`). Powabase
already implements that pipeline server-side, so our code becomes a thin,
secure proxy between a simple frontend and the Powabase API — not a
reimplementation of RAG internals.

## Non-goals (v1)

- Multi-tenant / per-user access control. This is a single-user/demo-scoped
  app: one Powabase project, one Knowledge Base, one Agent, no GoTrue user
  auth layer.
- Non-PDF ingestion (Word, images, URLs) — PDF only for v1.
- Streaming chat responses to the browser — the backend buffers Powabase's
  SSE stream and returns one JSON response per chat turn.
- Automated test suite — this wraps a live external API; verification is
  manual (curl / Swagger `/docs`), matching the spirit of the original
  tutorial.

## Prerequisite: Powabase project setup (human-only step)

A coding agent cannot create a Powabase account or read Studio-only secrets.
Before any code can run, a human must:

1. Create a Powabase project at app.powabase.ai.
2. Open **Connect** (project header, or `?showConnect=true`) → copy the
   **Project URL** and **Service Role (Secret) Key** into `backend/.env`.
3. Decide on a model provider. `model` on a Powabase agent is any LiteLLM
   model ID, so an OpenAI-compatible provider (Groq, Together, OpenRouter,
   a self-hosted vLLM/Ollama endpoint, etc.) works — no OpenAI account
   required. The provider key can be added either:
   - by hand in Studio → **Settings → LLM Provider Keys**, or
   - programmatically by our bootstrap script via `POST /api/ai-provider-keys`,
     if the key is supplied via env var.

The Service Role key must never reach the browser — it lives only in the
backend's `.env` and is used server-side.

## Architecture

```
rag-chatbot/
  backend/
    app/
      main.py                    # FastAPI app; mounts routers + serves frontend static files
      core/
        config.py                # Settings (pydantic-settings) + startup validation
      clients/
        powabase_client.py       # thin httpx wrapper: two-header auth, all /api/* calls
      services/
        ingest_service.py        # upload -> poll extracted -> add to KB -> poll indexed
        chat_service.py          # calls agent /run/stream, collects final answer + citations
      api/
        routes/
          health.py
          ingest.py
          chat.py
      models/
        schemas.py                # Pydantic request/response models
    scripts/
      bootstrap_powabase.py       # one-time: create KB + Agent (+ optional provider key), print IDs
    tmp/                          # scratch dir for uploaded PDFs before ingest
    .env.example
    requirements.txt              # fastapi, uvicorn, httpx, pydantic-settings, python-multipart, python-dotenv
  frontend/
    index.html
    app.js
    styles.css
  README.md
```

FastAPI serves the static frontend directly (`StaticFiles` mount) — one
process, no CORS configuration needed for local dev.

## Config (`backend/.env`)

| Var | Purpose |
| --- | --- |
| `POWABASE_BASE_URL` | Project URL, e.g. `https://{ref}.p.powabase.ai` |
| `POWABASE_SERVICE_ROLE_KEY` | Server-side only; used for both `apikey` and `Authorization: Bearer` headers |
| `POWABASE_KB_ID` | Knowledge Base ID (from bootstrap script or Studio) |
| `POWABASE_AGENT_ID` | Agent ID (from bootstrap script or Studio) |
| `POWABASE_AGENT_MODEL` | LiteLLM model ID for the agent (any OpenAI-compatible provider's model string) |
| `POWABASE_PROVIDER_NAME` / `POWABASE_PROVIDER_KEY` | Optional — only used by the bootstrap script if registering a provider key programmatically |

## Bootstrap script (`scripts/bootstrap_powabase.py`)

Run once, manually, after the human prerequisite step above:

1. Read `POWABASE_BASE_URL` + `POWABASE_SERVICE_ROLE_KEY` from `.env`.
2. If `POWABASE_PROVIDER_NAME`/`POWABASE_PROVIDER_KEY` are set, call
   `POST /api/ai-provider-keys` to register the provider key. Skip if the
   user configured it by hand in Studio instead.
3. Create a Knowledge Base (`chunk_embed` indexing / `hybrid` retrieval,
   platform defaults) if one with the configured name doesn't already exist
   (`GET /api/knowledge-bases` to check first).
4. Create an Agent with `model = POWABASE_AGENT_MODEL`, a system prompt
   instructing it to answer from the knowledge base, and link the KB
   (`POST /api/agents/{id}/knowledge-bases`) — this auto-adds the
   `knowledge_search` tool.
5. Print `POWABASE_KB_ID` / `POWABASE_AGENT_ID` for the user to paste into
   `.env`.

## Data flow — ingestion

```
POST /ingest/file (multipart PDF) → backend
  1. POST {POWABASE}/api/sources/upload
     - 201 → new source_id
     - 409 duplicate_source → reuse the id from the error body (treated as success, not an error)
  2. Poll GET /api/sources/{id} until extraction_status is terminal:
     - extracted            → continue
     - attention_required   → return 422: "low-quality/scanned PDF, needs OCR re-extraction"
                               (no automatic re-extraction in v1 — the message says how)
     - failed / cancelled    → return 500 with Powabase's error message
     - timeout (max wait exceeded) → return 202 with source_id + status "pending"
       so the caller can check back later, instead of hanging the request
  3. POST /api/knowledge-bases/{KB_ID}/sources {source_id} → triggers indexing
     (idempotent: re-adding an already-indexed source re-dispatches indexing)
  4. Poll the indexed_source until index_status == "indexed" (or "failed"), same
     timeout handling as step 2
  5. Return { source_id, status, chunks? } to the caller
```

## Data flow — chat

```
POST /chat { query, session_id? } → backend
  1. POST {POWABASE}/api/agents/{AGENT_ID}/run/stream
     { message: query, session_id?, citations_enabled: true }
  2. Consume the SSE stream server-side (httpx streaming); collect the final `complete` event
  3. Return JSON: { answer, session_id, citations, sources }
```

`session_id` is captured from the first response's `start` SSE event and
threaded through subsequent calls so multi-turn conversation stays in one
Powabase agent session.

## Error handling

- Config/startup: fail fast if any of `POWABASE_BASE_URL` /
  `POWABASE_SERVICE_ROLE_KEY` / `POWABASE_KB_ID` / `POWABASE_AGENT_ID` is
  missing. Additionally, on startup, make one real
  `GET /api/knowledge-bases/{id}` and `GET /api/agents/{id}` call each —
  refuse to start with a clear error if either 404s, rather than failing on
  the first real user request.
- `402 insufficient_credits` from Powabase → surface as-is to the caller, no
  retry (this is a billing wall, not a transient failure).
- `503` (billing service unreachable) → one retry with backoff, then
  surface the error.
- `provider_key_decrypt_failed` (from a chat call) → return a clear message
  pointing at Studio → Settings → LLM Provider Keys.

## Frontend

Plain HTML/JS, two panels:
- Upload widget — calls `POST /ingest/file`, shows status (indexed / pending / error).
- Chat panel — calls `POST /chat`, keeps `session_id` in memory for the
  browser session, renders `answer` + `sources`/citations.

No build tooling; served by FastAPI's `StaticFiles` mount.

## Testing / verification plan

Manual, via curl or the FastAPI Swagger UI (`/docs`):
1. `GET /health` — confirms config validation passed and Powabase is reachable.
2. `POST /ingest/file` with a sample PDF — confirms upload → extract → index completes.
3. `POST /chat` with a question about the uploaded PDF's content — confirms
   retrieval + generation works end-to-end, and that citations reference the
   uploaded source.
4. Re-run step 2 with the same file — confirms the 409-duplicate-source path
   is handled without error.
