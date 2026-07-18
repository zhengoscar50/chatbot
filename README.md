# RAG Chatbot on Powabase

A RAG chatbot backend (FastAPI) + simple frontend, backed by
[Powabase](https://powabase.ai)'s native Sources/Knowledge-Base/Agent
pipeline for ingestion, retrieval, and generation.

## 1. Create a Powabase project (one-time, human step)

1. Sign up / log in at https://app.powabase.ai and create a project.
2. Open **Connect** in the project header, copy the **Project URL** and
   **Service Role (Secret) Key**.
3. Copy `backend/.env.example` to `backend/.env` and fill in
   `POWABASE_BASE_URL` and `POWABASE_SERVICE_ROLE_KEY`.
4. Decide on a model provider. Set `POWABASE_AGENT_MODEL` to any LiteLLM
   model ID (e.g. `gpt-4o-mini`, `groq/llama-3.1-70b-versatile`,
   `openrouter/<org>/<model>`). Then either:
   - add the provider's key by hand in Studio → **Settings → LLM Provider
     Keys**, or
   - set `POWABASE_PROVIDER_NAME` / `POWABASE_PROVIDER_KEY` in `.env` and
     let the bootstrap script register it for you.

## 2. Install dependencies

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## 3. Run the bootstrap script

Creates the Knowledge Base and Agent (idempotent — safe to re-run):

```bash
python -m scripts.bootstrap_powabase
```

Copy the printed `POWABASE_KB_ID` and `POWABASE_AGENT_ID` into `backend/.env`.

## 4. Run the app

```bash
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/ for the chat UI, or http://127.0.0.1:8000/docs
for the Swagger UI.

## 5. Manual verification checklist

- [x] `GET /health` returns `200` with your configured `kb_id`/`agent_id`/`model`.
- [x] `POST /ingest/file` with a real PDF returns `{"source_id": ..., "status": "indexed"}`.
- [x] `POST /chat` with a question about that PDF's content returns an answer
      grounded in it, with non-empty `citations`.
- [x] Re-running `POST /ingest/file` with the *same* file succeeds without
      error (exercises the `409 duplicate_source` path).

Verified 2026-07-18 against a live Powabase project with `openrouter/openai/gpt-oss-20b:free`.
Along the way, two real bugs surfaced and were fixed (see git history): the
SSE parser assumed a literal `event:` line that Powabase's actual stream
never sends (the event type lives inside the JSON body's `event` key
instead — see `references/streaming-sse.md` in the Powabase skill), and the
final-answer field is `content`, not `answer`. `ChatService` and the `/chat`
route were updated to match the real wire format and to surface a clear
error when an agent run fails downstream (e.g. a provider rejects the call)
instead of a bare 500.

```bash
curl http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/ingest/file \
  -F 'file=@/path/to/your.pdf'

curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What does this document say about X?"}'
```

## Running tests

```bash
cd backend
pytest -v
```
