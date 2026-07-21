# RAG Chatbot on Powabase

A RAG chatbot backend (FastAPI) + simple frontend, backed by
[Powabase](https://powabase.ai)'s native Sources/Knowledge-Base/Agent
pipeline for ingestion, retrieval, and generation. Supports multiple
**profiles**, each with its own isolated documents.

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
   - set `POWABASE_PROVIDER_NAME` / `POWABASE_PROVIDER_KEY` in `.env` (the
     bootstrap script in step 3 below registers it for you).

You do **not** need `POWABASE_KB_ID` / `POWABASE_AGENT_ID` — each profile
creates and manages its own Knowledge Base and agent automatically.

## 2. Install dependencies

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## 3. (Optional) Register a provider key

`scripts/bootstrap_powabase.py` is now optional — profiles auto-provision
their own resources, so you no longer need to pre-create a KB/agent. Its one
remaining use is registering a BYOK provider key from your `.env` if you set
`POWABASE_PROVIDER_NAME` / `POWABASE_PROVIDER_KEY` (equivalent to adding the
key in Studio → Settings → LLM Provider Keys):

```bash
python -m scripts.bootstrap_powabase
```

## 4. Run the app

```bash
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/ for the chat UI, or http://127.0.0.1:8000/docs
for the Swagger UI.

## 5. Profiles & data isolation

Each **profile** has its own isolated Knowledge Base and agent. Type a name
in the Profile bar at the top and press Enter — the first time a name is used,
its Knowledge Base and agent are created automatically. Documents you upload
are added only to the current profile's Knowledge Base, and chats only search
that profile's documents.

Switching to a different profile clears the conversation and routes everything
to that profile's isolated data. A document uploaded under `alice` is not
visible to `bob`.

**Scope note:** this is a demonstration of *data isolation*, not access
control. There are no passwords — anyone using the app can select any profile
name.

**Limitations (by design, for the local demo):**

- **Run single-worker** (the default `uvicorn app.main:app --reload` is). The
  profile→resources map is cached per process; under `--workers N` two workers
  could concurrently provision the same new profile and create duplicate
  Knowledge Bases, breaking that profile's retrieval. Single-worker avoids this.
- **Profile names are matched by a normalized slug** (lowercased, trimmed,
  non-alphanumeric runs collapsed to `-`). So `Alice`, `alice`, and
  `alice!` all map to the same profile and share its data.

## 6. Verification

Automated suite (faked Powabase, no network):

```bash
cd backend
pytest -v
```

Manual isolation proof, verified 2026-07-21 against a live Powabase project
with `openrouter/nvidia/nemotron-3-super-120b-a12b:free`:

- [x] `GET /health` returns `{"status": "ok", "model": ...}`.
- [x] Under profile `alice`, `POST /ingest/file` (with `profile=alice`) indexes a
      PDF, and `POST /chat` answers a question about it with a citation.
- [x] Under profile `bob`, the same question returns "not able to find any
      information … in the provided knowledge base" with zero citations — bob
      cannot see alice's document.
- [x] Switching back to `alice` still answers correctly — the document is
      isolated to alice, not lost.

```bash
# provision a profile (auto-creates its KB + agent on first use)
curl -X POST http://127.0.0.1:8000/profile \
  -H "Content-Type: application/json" -d '{"profile": "alice"}'

# upload a PDF into that profile's knowledge base
curl -X POST http://127.0.0.1:8000/ingest/file \
  -F 'profile=alice' -F 'file=@/path/to/your.pdf'

# ask a question, scoped to that profile
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What does this document say about X?", "profile": "alice"}'
```

## Notes on the Powabase wire format

Two Powabase-specific details, discovered during earlier live verification and
handled in the code: agent-run responses stream events whose type lives inside
each JSON body's `event` key (there is no literal SSE `event:` line), and the
final answer text is in the `content` field, not `answer`. `ChatService` and
`app/clients/sse.py` match this real wire format and surface a clear error when
an agent run fails downstream (e.g. a provider rejects or rate-limits the call)
instead of a bare 500.
