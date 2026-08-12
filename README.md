# RAG Chatbot on Powabase

A RAG chatbot backend (FastAPI) + simple frontend, backed by
[Powabase](https://powabase.ai)'s native Sources/Knowledge-Base/Agent
pipeline for ingestion, retrieval, and generation. Users create their own
**agents** — each with its own instructions, model and a permanent knowledge
base they train — and hold many chats with each agent. Training persists across
chats; documents uploaded inside a chat stay in that chat.

An **orchestrator** picks which agent answers each individual message, so one
conversation can move between specialists and every turn records who answered
it. Retrieval is attached per question rather than configured on the agent.

**Status:** a working demo, not a hardened product. Single-worker by design,
the admin gate is demo-grade (see §7), and agents are private to their creator
with no sharing. Read the scope notes before running it for anyone else.

Requires Python 3.9+. Licensed under the [MIT License](LICENSE).

## 1. Create a Powabase project (one-time, human step)

1. Sign up / log in at https://app.powabase.ai and create a project.
2. Open **Connect** in the project header, copy the **Project URL** and
   **Service Role (Secret) Key**.
3. Copy `backend/.env.example` to `backend/.env` and fill in
   `POWABASE_BASE_URL` and `POWABASE_SERVICE_ROLE_KEY`.
4. Decide on a model provider, and register its key either by hand in
   Studio → **Settings → LLM Provider Keys**, or by setting
   `POWABASE_PROVIDER_NAME` / `POWABASE_PROVIDER_KEY` in `.env` (the bootstrap
   script in step 3 below registers it for you).
5. Choose models. Any LiteLLM model ID works (e.g. `gpt-4o-mini`,
   `claude-sonnet-5`, `groq/llama-3.1-70b-versatile`). Three settings matter,
   each defaulting to `gpt-4o-mini`:

   | Variable | Decides |
   |---|---|
   | `ORCHESTRATOR_MODEL` | which agent answers each message |
   | `DEFAULT_AGENT_MODEL` | the model for new agents whose creator picks none |
   | `GENERAL_ASSISTANT_MODEL` | the fallback when no agent fits |

   `POWABASE_AGENT_MODEL` is **not** one of them. Nothing but the optional
   bootstrap script reads it — setting it changes no behaviour. `GET /health`
   reports the three above, so you can always see what a deployment is really
   running.

You do **not** need `POWABASE_KB_ID` / `POWABASE_AGENT_ID` — each agent
creates and manages its own Knowledge Bases automatically.

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

## 5. Create the tables (one-time)

Paste each file in `backend/migrations/` into the Powabase Studio **SQL Editor**
in order (or run them via the Database URL). The app can't save anything until
they exist.

`004_user_owned_agents.sql` is **destructive**: it creates `public.agents` and
drops/recreates `public.sessions`, discarding any existing chats. That is
deliberate — `sessions.agent_id` changed meaning from a Powabase agent id to a
foreign key into `public.agents`.

`007_shared_scratch_kb.sql` adds `sessions.source_ids` and must be applied
**before** deploying the code that uses it: without the column, a chat upload
indexes but cannot be recorded, so it silently never becomes answerable. It is
non-destructive and safe to apply while the old code is still running — the
column defaults to an empty list, which the previous release ignores.

## 6. Agents and chats

Click **+** in the sidebar to create an agent. You give it:

- a **name**
- **instructions** — its system prompt, e.g. "You are a study tutor for AP
  Chemistry. Always show your working."
- a **model**, picked from a list — or **Other…** to type any LiteLLM id
- a **grounding** mode — *only answer from my documents* (strict) or *use my
  documents, but answer freely* (open)
- whether it may also use the shared **general knowledge** base

Then open **⚙ Manage** to train it: upload PDFs into its permanent knowledge
base, see what it has been trained on, and remove a document you didn't mean to
add. Training persists — every future chat with that agent can use it.

Chats are listed under the selected agent. Each chat also has its own **scratch**
documents: a PDF you attach inside a chat is answerable there and nowhere else,
so you can drop in a one-off document without permanently teaching the agent.

Retrieval for one question spans up to four knowledge bases: the agent's two
permanent ones (chunked and full-document), this chat's scratch documents, and
the shared general KB if the agent opted in.

Those knowledge bases are attached **to the run itself** rather than searched
up front: the agent gets a `knowledge_search` tool over exactly the bases in
scope for that one question and decides what to look up. Chat uploads all live
in a single shared scratch KB, and a chat sees only its own because retrieval
names that chat's `source_ids`. A chat that has uploaded nothing contributes no
scratch scope at all — the shared KB is never searched unscoped, which is what
keeps one chat's uploads out of another's answers.

The model list is hand-maintained in `AGENT_MODELS` (see `app/core/config.py`),
because Powabase publishes no model catalog. Every id in the default list
answered a live ping on 2026-08-07. Since a hand-maintained list drifts, two
things guard it: **Other…** lets you use an id the list doesn't know, and
creating an agent (or changing its model) probes the model first — a dead or
mistyped id fails in the dialog with a clear message instead of becoming an
agent that breaks on its first message.

**Scope note:** agents are private to their creator. There is no sharing.

**Run single-worker** (the default `uvicorn app.main:app --reload` is). Agent
resources are provisioned per request; running multiple workers is untested.

## 7. Admin: shared general knowledge

Set `ADMIN_PASSWORD` in `backend/.env` to enable the admin feature. Then open
`/admin` (there's an "Admin" link at the bottom of the sidebar), enter the
password, and upload PDFs into the shared **general knowledge** base.

Unlike before, general knowledge is **opt-in per agent** rather than automatic:
an agent uses it only if its creator ticked "Also use shared general knowledge".
If `ADMIN_PASSWORD` is not set, the admin endpoints are disabled and the rest of
the app runs normally.

**Scope note:** the admin password is checked server-side but sent with each
admin request — a demo-grade gate, not hardened authentication.

## 8. Verification

Automated suite (faked Powabase, no network):

```bash
cd backend
pytest -v
```

Manual proof (run the migrations in step 5 first), against a live Powabase
project. Everything is scoped to the logged-in user, so pass a bearer token:

- [ ] Create an agent → 201, listed by `GET /agents` with `trained: false`.
- [ ] `POST /sessions` with someone else's `agent_id` → **404**.
- [ ] Train a PDF into it → listed by `GET /agents/{id}/documents`; `trained`
      flips to `true`.
- [ ] A **brand new chat** with that agent answers from the trained document,
      with a citation. This is the point of the feature: training outlives the
      chat it was added in.
- [ ] A PDF attached *inside* a chat is answerable there, but a second chat with
      the same agent can't see it.
- [ ] Edit the instructions → the next answer changes, and `powabase_agent_id`
      is unchanged (edits patch in place rather than recreating).
- [ ] An agent with `use_general_kb: false` doesn't answer from general
      knowledge; flipping it to `true` makes the same question answerable.
- [ ] Untrain the document → gone from `GET /agents/{id}/documents`.
- [ ] Delete the agent → its chats go with it, and `GET /agents` no longer
      lists it.

```bash
TOKEN=... # from POST /auth/login

# create an agent
curl -X POST http://127.0.0.1:8000/agents \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Chem tutor", "instructions": "Always show your working.",
       "grounding": "strict"}'

# train it — this is permanent, and every future chat can use it
curl -X POST http://127.0.0.1:8000/agents/<agent_id>/train \
  -H "Authorization: Bearer $TOKEN" -F 'file=@/path/to/your.pdf'

# start a chat with it
curl -X POST http://127.0.0.1:8000/sessions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_id": "<agent_id>"}'

# ask
curl -X POST http://127.0.0.1:8000/chat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"session_id": "<id>", "query": "What does this document say about X?"}'
```

## Deploying

`deploy/README.md` is the runbook: an AWS free-tier `t3.micro` behind a
Cloudflare tunnel, with no inbound web ports open. Two things matter before
you share a URL:

- **Set `SIGNUP_INVITE_CODE`.** Otherwise anyone the link reaches can register
  and spend your LLM credits.
- **Apply the migrations first.** Powabase has no SQL endpoint, so every
  numbered file in `backend/migrations/` is pasted into the Studio SQL Editor
  by hand — including after an update.

## Notes on the Powabase wire format

Powabase-specific details, each discovered during live verification and handled
in the code:

- Agent-run responses stream events whose type lives inside each JSON body's
  `event` key (there is no literal SSE `event:` line), and the final answer text
  is in the `content` field, not `answer`. `ChatService` and `app/clients/sse.py`
  match this real wire format and surface a clear error when an agent run fails
  downstream (e.g. a provider rejects or rate-limits the call) instead of a bare
  500.
