# Chatbots, phase 1: the entity and its scoping

## The change

Today a user owns agents directly, and every chat routes across all of them.
This inserts a layer: a user owns **chatbots**, a chatbot owns **agents** and
**chats**, and routing considers only the agents belonging to that chat's
chatbot.

Phase 1 delivers exactly that and nothing more. Knowledge stays where it is.

**The property that makes this safe to ship:** after the backfill, behaviour is
identical to today. Every existing agent and chat lands in one auto-created
chatbot per user, so the same roster answers the same questions. Anything that
changes is a bug, which makes the change easy to judge.

Phase 2 (separate spec) moves knowledge down a level.

## Why a layer rather than more per-chat controls

Per-chat agent exclusion already exists and solves a narrower problem: muting a
few agents for one conversation. It does not give separate *workspaces* — a
chat still starts from the whole roster, agents are global to the user, and
there is no way to keep a personal set and a work set apart.

Exclusion survives this change. Narrowing within a chatbot that holds a dozen
agents is still useful, and it is now scoped to a smaller roster, which is what
it was always for.

## Data model

Migration `011`, in three parts, applied in one paste.

```sql
create table if not exists public.chatbots (
  id          uuid primary key default gen_random_uuid(),
  owner_id    uuid not null,
  name        text not null,
  description text not null default '',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists chatbots_owner_updated_idx
  on public.chatbots (owner_id, updated_at desc);
alter table public.chatbots enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.

alter table public.agents   add column if not exists chatbot_id uuid;
alter table public.sessions add column if not exists chatbot_id uuid;
```

`chatbot_id` is **nullable** and carries no foreign key in this migration. Both
are deliberate:

- Nullable means the currently deployed code keeps working the moment the
  migration is applied. There is no window where the running release is broken.
- No FK because the backfill populates the column immediately afterwards, and a
  NOT NULL FK would make the migration itself fail on existing rows.

Tightening to NOT NULL is a follow-up once the backfill is verified, not part
of this change.

## The backfill

This is the first migration that **rewrites existing rows** rather than adding a
defaulted column. `004` discarded chats deliberately; this one must lose
nothing.

```sql
-- One chatbot per user who owns anything.
insert into public.chatbots (owner_id, name, description)
select distinct owner_id, 'My chatbot',
       'Everything that existed before chatbots were introduced.'
from (
  select owner_id from public.agents
  union
  select owner_id from public.sessions
) owners
where not exists (
  select 1 from public.chatbots c where c.owner_id = owners.owner_id
);

-- Stamp every agent and chat with their owner's chatbot.
update public.agents a
   set chatbot_id = c.id
  from public.chatbots c
 where c.owner_id = a.owner_id and a.chatbot_id is null;

update public.sessions s
   set chatbot_id = c.id
  from public.chatbots c
 where c.owner_id = s.owner_id and s.chatbot_id is null;
```

The `not exists` guard makes the insert idempotent, so a re-run cannot create a
second chatbot per user. Both updates are guarded by `chatbot_id is null`, so
re-running never re-stamps a row that has since moved.

**Current data, for verification.** Counts must match exactly afterwards:

| User | Agents | Chats |
|---|---|---|
| oscarzheng | 11 | 18 |
| oscar | 5 | 4 |
| zheng | 0 | 3 |
| **Total** | **16** | **25** |

Verification, run after applying:

- three rows in `chatbots`, one per user (`zheng` owns chats, so qualifies);
- `select count(*) from agents where chatbot_id is null` returns 0;
- same for `sessions`;
- per-user agent and chat counts unchanged from the table above.

## Scoping

**`ChatbotService`** — new, small: create, list, rename, delete, and
`get_owned(chatbot_id, owner_id)` mirroring the existing pattern.

**`AgentService`** — `create` takes a `chatbot_id`; `list` takes a chatbot
rather than an owner. `get_owned` keeps its `owner_id` check unchanged: a
chatbot narrows what you see, it does not weaken who you are.

**`SessionService`** — `create_session` records the chatbot; `list` is scoped to
one.

**Routing** — one line in `chat.py`. The roster comes from the chat's
`chatbot_id`, then per-chat exclusions apply as they do today.

**Deleting a chatbot** deletes its agents and chats, reusing the existing
cleanup so knowledge bases and Powabase agents go with them. A user's last
chatbot cannot be deleted — otherwise agents have nowhere to live and the next
login has nothing to show.

**A new user gets a chatbot at registration.** The backfill only covers users
who already own something, so without this a freshly registered account would
have nowhere to put its first agent. `AuthService.register` creates "My
chatbot" alongside the user row. Registration is also the only place this can
happen exactly once — creating it lazily on first list would race two parallel
requests into two chatbots.

**The orchestrator and general assistant are unaffected.** Both remain
project-wide singletons. Neither holds a roster or knowledge of its own — the
roster is built per request and passed in the message — so a layer above agents
changes nothing for them. The general assistant continues to answer when no
agent in the chatbot fits.

## Routes

New: `POST /chatbots`, `GET /chatbots`, `PATCH /chatbots/{id}`,
`DELETE /chatbots/{id}`.

Changed: `GET /agents` and `GET /sessions` take a required `chatbot_id`;
`POST /agents` and `POST /sessions` record it. `POST /chat` reads the chatbot
from the chat row rather than the request, so a client cannot ask one chatbot's
question against another's roster.

Unchanged: every ownership check, and the `404`-not-`403` convention that keeps
a foreign id indistinguishable from a missing one.

## UI

The sidebar gains a chatbot picker above the chat list. Selecting one filters
the chats below it, and scopes **⚙ Manage agents** to that chatbot. A "New
chatbot" action sits with the picker.

The topbar agent-scope control keeps working, now over the chatbot's roster.

State lives in `app.js` beside `currentSessionId`: a `currentChatbotId`,
persisted to `localStorage` so a reload returns you to the chatbot you were in.

## What phase 1 does NOT do

- **Knowledge stays user-wide.** Personal knowledge and the shared general base
  are untouched, so retrieval results do not move while the foundation changes.
  That is phase 2.
- **No sharing.** Chatbots are private to their owner, like agents today.
- **No NOT NULL / FK tightening.** A follow-up once the backfill is verified.
- **No moving agents between chatbots.** Create them where you want them.

## Testing

Unit, following the existing patterns:

- `ChatbotService` — create, list, ownership, refusing to delete the last one.
- Registration creates exactly one chatbot for a new user.
- `AgentService.list` scoped to a chatbot; an agent in another chatbot is not
  visible even to its owner.
- Roster filtering: a chat routes only across its own chatbot's agents.
- `POST /chat` ignores any chatbot in the request body.
- Route tests for the new endpoints, including `404` for another user's chatbot.

Live, after deploying:

- The chatbot picker lists "My chatbot"; every existing agent and chat is inside
  it; asking the eyewash question still routes to Chem Tutor.
- A second chatbot with one agent: its chat routes only to that agent, and the
  first chatbot's agents are invisible from it.
- Counts before and after the backfill match the table above.

## Risks

**The backfill is the risk.** It rewrites 41 live rows. Mitigations: idempotent
guards, nullable columns so the running release is unaffected, and count
verification before the new code is deployed. Recovery if the stamping is
wrong: `chatbot_id` can be cleared and the update re-run, because nothing else
depends on it until the new code ships.

**Ordering.** The migration and backfill must be applied *before* the new code
deploys. Applied early they are harmless — the running release ignores both
columns.

**A silent narrowing.** If a chat ends up in a different chatbot from its
agents, routing quietly finds an empty roster and every answer comes from the
general assistant. This is why the live check asks a question with a known
answer rather than just confirming the page loads.
