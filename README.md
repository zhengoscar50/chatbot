# RAG Chatbot on Powabase

A RAG chatbot backend (FastAPI) + simple frontend, backed by
[Powabase](https://powabase.ai)'s native Sources/Knowledge-Base/Agent
pipeline for ingestion, retrieval, and generation. Supports per-user **saved
sessions** (each with its own isolated documents) plus an admin-curated shared
general-knowledge base.

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

## 5. Create the sessions table (one-time)

Sessions are stored in a `public.sessions` table. Create it once by pasting
`backend/migrations/001_create_sessions.sql` into the Powabase Studio **SQL
Editor** (or running it via the Database URL). The app can't save sessions
until this table exists.

## 6. Sessions & per-session isolation

Type a **user** name in the sidebar, then create **sessions** — saved,
resumable conversations. Each session has its own isolated documents: a PDF
uploaded in one session is never visible to another session or user. Sessions
are listed by name in the left sidebar; click one to resume it. The first
message you send names the session.

**Scope note:** still a demonstration of *data isolation*, not access control
— users are passwordless names.

**Run single-worker** (the default `uvicorn app.main:app --reload` is). Session
resources are provisioned per request; running multiple workers is untested.

## 7. Admin: shared general knowledge

Set `ADMIN_PASSWORD` in `backend/.env` to enable the admin feature. Then open
`/admin` (there's an "Admin" link at the bottom of the sidebar), enter the
password, and upload PDFs into the shared **general knowledge** base.

Every **new** session's chatbot answers from general knowledge **plus** that
session's own uploaded documents. Sessions created before general knowledge was
added keep only their own documents (new-sessions-only). If `ADMIN_PASSWORD` is
not set, the admin endpoints are disabled and the rest of the app runs normally.

**Scope note:** the admin password is checked server-side but sent with each
admin request — a demo-grade gate, not hardened authentication.

Verified live 2026-07-24 (`ADMIN_PASSWORD` set, model
`openrouter/nvidia/nemotron-3-super-120b-a12b:free`): wrong admin password →
401, correct → 200; a general-knowledge PDF trained via `/admin/train` was
answered (with a citation) by a **new** session that had uploaded nothing of its
own; and that same session also answered from its own later upload — i.e. the
session drew on general knowledge **plus** its own documents.

## 8. Verification

Automated suite (faked Powabase, no network):

```bash
cd backend
pytest -v
```

Manual isolation + resume proof (run the migration in step 5 first). Verified
2026-07-24 against a live Powabase project with
`openrouter/nvidia/nemotron-3-super-120b-a12b:free`:

- [x] User `alice`, **New session**, upload a PDF, ask about it → cited answer;
      the session is named from your first message.
- [x] **New session** again, ask about the *first* session's document → not
      found (the second session can't see it; zero citations).
- [x] Reopen the first session → its messages (question + answer) are still there.
- [x] User `bob` has zero of alice's sessions.
- [x] Sessions persist in the `public.sessions` table (listed via `GET /sessions`).

```bash
# create a session for a user (provisions its own KB + agent)
curl -X POST http://127.0.0.1:8000/sessions \
  -H "Content-Type: application/json" -d '{"user": "alice"}'

# upload a PDF into that session's knowledge base
curl -X POST http://127.0.0.1:8000/ingest/file \
  -F 'session_id=<id from above>' -F 'file=@/path/to/your.pdf'

# ask a question, scoped to that session
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<id>", "query": "What does this document say about X?"}'
```

## Notes on the Powabase wire format

Powabase-specific details, each discovered during live verification and handled
in the code:

- Agent-run responses stream events whose type lives inside each JSON body's
  `event` key (there is no literal SSE `event:` line), and the final answer text
  is in the `content` field, not `answer`. `ChatService` and `app/clients/sse.py`
  match this real wire format and surface a clear error when an agent run fails
  downstream (e.g. a provider rejects or rate-limits the call) instead of a bare
  500.
