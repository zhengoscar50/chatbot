# Sharing and embedding a chatbot

## The change

Today a chatbot cannot leave its owner's account. This adds an unlisted link
and an embeddable iframe for a single chatbot, so someone without an account
can use it.

It is the first feature that makes this project useful to anyone but its owner.
Everything before it made the thing you own better organised.

## The audience this is built for

**A handful of people you send the link to** — a recruiter, a classmate, a
friend. Not a public product with continuous anonymous traffic. That decision
sets the whole risk budget: the link is unlisted rather than secret, and the
defence against it being forwarded or indexed is a **daily message cap**, not
authentication.

Designing for continuous public traffic would need per-visitor rate limiting,
abuse handling, and a spend ceiling. Those are deliberately not here.

## Why a cap is not optional

There is no rate limiting anywhere in this application. `/chat` is built
entirely around `get_current_user` and `get_owned_session`, and registration is
invite-gated — which is the only reason unbounded spend has never mattered.

A public route removes both. Every anonymous message spends the owner's
Powabase credits. The cap is the feature's load-bearing safety property, not a
nicety.

## The shape

```
owner                              visitor
  │                                   │
  ├─ ⋯ → Share                        │
  │    POST /chatbots/{id}/share      │
  │    → token, link, embed snippet   │
  │                                   ▼
  │                     GET  /s/{token}          public page, no login
  │                     POST /s/{token}/session  their own flagged chat
  │                     POST /s/{token}/chat     capped, then answered
  │                                   │
  └─ reads their transcripts ◄────────┘
```

## Data model

Migration `014`, applied by hand in Powabase Studio like every other.

```sql
alter table public.chatbots add column if not exists share_token       text;
alter table public.chatbots add column if not exists share_daily_limit int  not null default 100;
alter table public.chatbots add column if not exists share_used_today  int  not null default 0;
alter table public.chatbots add column if not exists share_used_date   date;

create unique index if not exists chatbots_share_token_idx
  on public.chatbots (share_token) where share_token is not null;

alter table public.sessions add column if not exists shared boolean not null default false;
```

`share_token` is null when the chatbot is not shared, which is the only "is it
shared" state — there is no separate boolean to fall out of sync with it.

**Storing the date beside the count is what makes "resets at midnight" need no
scheduled job.** A request arriving on a new date resets the counter in the same
write that increments it. Nothing has to run at midnight, and a chatbot nobody
visits for a month costs nothing to keep correct.

The partial unique index enforces that two chatbots can never share a token,
while still allowing many chatbots to have none.

### The `shared` flag does two jobs

1. It keeps visitor chats out of the owner's sidebar.
2. It is the gate proving a session belongs to a visitor rather than to the
   owner — see the security section, where that second job matters more.

## Extracting the answering core

The public chat route must not duplicate orchestration. Today `/chat`'s handler
does ownership, history, roster, routing, retrieval scope, the run, and
persistence in one body. That body moves to:

```python
answer_turn(deps, session_row, chatbot_row, query) -> ChatResponse
```

`/chat` keeps its authentication and ownership checks and then calls it.
`/s/{token}/chat` does its own token, session and cap checks and then calls the
same function.

**This is the largest risk in the feature.** It moves the most
security-sensitive and best-tested code in the application. It is also the only
alternative to two copies of the routing logic drifting apart — and the copy
that drifts would be the one strangers can reach.

The existing `/chat` tests must pass unchanged afterwards. If a test needs
editing to accommodate the move, the move changed behaviour and is wrong.

## Routes

### Owner-facing, authenticated

| Route | Does |
|---|---|
| `POST /chatbots/{id}/share` | Create or **regenerate** the token. Returns `{token, url, embed}` |
| `DELETE /chatbots/{id}/share` | Revoke: set `share_token` to null |
| `GET /chatbots/{id}/share` | Current state: token or null, daily limit, used today |

Regenerating is how revocation-and-reissue works: the old link dies the moment
a new token is written.

### Public, unauthenticated

| Route | Does |
|---|---|
| `GET /s/{token}` | Serves the public chat page |
| `GET /s/{token}/info` | `{name, description}` for the page header |
| `POST /s/{token}/session` | Creates a session with `shared = true`, returns its id |
| `POST /s/{token}/chat` | `{session_id, query}` → the answer |

**These must be registered before the static mount.** `main.py` mounts
`StaticFiles` at `/`, and its own comment records that the mount swallows
anything registered after it.

## Security

Three checks run in order on `/s/{token}/chat`, all before any work:

1. **The token resolves to a chatbot.** An unknown token is `404`, never `403`
   — the same rule the rest of the app follows, so a guessed token cannot be
   confirmed.
2. **The session belongs to that chatbot AND has `shared = true`.**
3. **The daily cap has room.**

**Check 2 is the sharpest edge in this design.** Without the `shared = true`
half, a visitor could pass one of the owner's own session ids and read or inject
into a private conversation. Chatbot membership alone is not enough, because the
owner's chats live in the same chatbot as the visitors'.

The public page receives the chatbot's name and description and nothing else —
no agent ids, no knowledge, no chat list, no username.

Visitors cannot upload. `/s/{token}/chat` accepts a query and nothing else, so
no visitor writes to the shared scratch knowledge base.

## What the visitor sees

A single page: the chatbot's name, a message thread with markdown rendering,
the name of the agent that answered, and its citations.

**Citations are shown deliberately**, including document filenames and quoted
excerpts. That is the cost of a convincing demo, and it means anyone with the
link learns the filenames in that chatbot's knowledge. Do not share a chatbot
whose filenames are sensitive.

No sidebar, no uploads, no settings, no theme toggle, no link back to the app.

At the cap:

> This demo has reached its limit for today — try again tomorrow.

Files: `frontend/share.html` and `frontend/share.js`, reusing `markdown.js`
unchanged. The public page shares no JavaScript with the authenticated app, so
nothing about accounts or agents can leak into it by accident.

## Reading visitor conversations

Almost free, because the sessions are owned by the owner: the existing
owner-gated `GET /sessions/{id}/messages` already works on them.

Two small changes complete it:

- `GET /sessions?chatbot_id=X` gains an optional `shared` filter, defaulting to
  **false** so the owner's sidebar excludes visitor chats.
- `GET /sessions?chatbot_id=X&shared=true` lists them for a read-only view.

The dashboard card shows a count when a chatbot is shared: `12 visitor chats`.

## The sharing UI

The dashboard's `⋯` menu gains **Share**, opening a modal with:

- the link, with a copy button;
- the embed snippet, with a copy button:
  `<iframe src="https://…/s/TOKEN" width="420" height="640" style="border:0"></iframe>`
- the daily limit and today's usage (`31 / 100 used today`);
- **Regenerate link** and **Stop sharing**.

A card for a shared chatbot shows a small indicator so the dashboard answers
"what am I currently exposing?" at a glance.

## Testing

Unit, following existing patterns:

- Token creation, regeneration and revocation; regeneration kills the old token.
- Unknown token → 404 on every public route.
- **A visitor cannot use one of the owner's session ids** — the same chatbot,
  `shared = false` → 404. This is the test that matters most.
- The cap: allowed under the limit, 429 at it, and **a request on a new date
  resets the counter** rather than carrying yesterday's total.
- `GET /sessions` excludes shared sessions by default and includes them with
  `shared=true`.
- The public chat route rejects anything beyond a query — no uploads.
- Every existing `/chat` test passes **unchanged** after the extraction.

Live, after deploying:

- Open the link in a private window: it answers, names the agent, shows
  citations.
- Two private windows do not see each other's messages.
- The owner's sidebar does not show visitor chats; the card counts them.
- Regenerate, then reload the old link: it is gone.
- The embed snippet renders in an iframe on a plain HTML page.

## Risks

**The cap has a race.** Two simultaneous requests can both pass the check before
either increments. At this scale it costs one extra message, not a breach.
Accepted rather than adding locking.

**A forwarded link is a public link.** The daily cap is the automatic defence;
regeneration is the manual one. There is no way to un-send a link.

**Storing strangers' text.** Visitor questions live in the owner's database
indefinitely, under the owner's account.

**The extraction.** Covered above — the feature's largest risk.

## Out of scope

- Visitor uploads.
- Per-visitor rate limiting, and any per-IP logic.
- Analytics beyond reading transcripts.
- Custom branding or theming of the public page.
- More than one link per chatbot.
- Any authentication on the public page, including a passcode.
- Expiring links.
