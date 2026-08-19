# Chatbots, phase 2: knowledge moves down a level

## The change

Phase 1 gave a user chatbots, and gave each chatbot its own agents and chats.
Knowledge stayed where it was, so every chatbot still read the same documents.
Phase 2 finishes the separation: **a chatbot owns its knowledge**, and the two
user-wide tiers that made that impossible are removed.

Personal knowledge moves down to the chatbot. The admin-curated general
knowledge base is deleted outright.

**The property that makes this judgeable:** every user has exactly one chatbot
today, so after the migration each user's documents are exactly where they were
— reachable from the one chatbot that holds everything. Retrieval results for
existing chats should not move. Anything that does move is a bug.

## Knowledge tiers, before and after

| Tier | Scope | Who reads it | Phase 2 |
|---|---|---|---|
| Agent's permanent KBs | one agent | that agent only | unchanged |
| Personal knowledge | one user | all their agents | **becomes chatbot knowledge** |
| General knowledge | the whole deployment | agents that opted in | **deleted** |
| Chat scratch | one chat | whoever answers there | unchanged, gains promote |

After this change there are three tiers, each owned by exactly one thing:
an agent, a chatbot, or a chat.

## Why the general KB goes

It is the one place in the application where one account's upload reaches
another account's answers. That was its purpose — a shared reference corpus
everyone starts from — but it does not survive the chatbot model. "Everyone
shares this" has no owner to hang off, and the same effect is now available by
putting the document in whichever chatbot needs it.

Removing it also removes the per-agent `use_general_kb` opt-in, which is the
only retrieval flag in the app and the only reason two agents in the same
container can see different documents.

## Chatbot knowledge is automatic

Every agent in a chatbot reads that chatbot's knowledge, always — including the
general assistant. There is no per-agent toggle.

The chatbot is already the boundary. A document is in a chatbot because it
should be known there; an agent inside that ignores it is a strange thing to
want, and a toggle is a second place to hunt when an answer comes back thin.
Per-chat agent exclusion, from the previous release, still covers "not this
agent, not right now."

This carries across the behaviour of personal knowledge, which is the tier
actually moving. The opt-in belonged to the tier being deleted.

## Data model

Migration `012`, applied in one paste.

```sql
alter table public.chatbots add column if not exists kb_id      uuid;
alter table public.chatbots add column if not exists kb_full_id uuid;
```

Two tiers for the same reason agents and users have two: a short document is
indexed whole, a long one is chunked. Both stay lazily created, so a chatbot
that is never trained costs no knowledge base.

### The migration

```sql
update public.chatbots c
   set kb_id = u.kb_id, kb_full_id = u.kb_full_id
  from public.users u
 where u.id = c.owner_id
   and c.kb_id is null and c.kb_full_id is null
   and c.id = (select c2.id from public.chatbots c2
                where c2.owner_id = u.id
                order by c2.created_at asc
                limit 1);
```

**The subselect is the whole safety argument.** "Every user has exactly one
chatbot" is true when this spec is written and stops being true the moment
anyone creates a second one. Without the guard, a user with two chatbots gets
the same `kb_id` written to both — two chatbots reading one knowledge base,
which is precisely the leak this phase exists to prevent. Restricting to each
owner's oldest chatbot is correct whenever it runs, including after the user
has created more.

The `is null` guards make the update idempotent: re-running never re-stamps a
chatbot whose knowledge has since diverged.

### What is deliberately not dropped

`users.kb_id`, `users.kb_full_id` and `agents.use_general_kb` stay in the
schema and stop being read. Dropping a column in the same migration that stops
using it means a rollback loses data. They can be dropped by hand later, once
the release has been live long enough to trust.

The live `general-knowledge-kb` in Powabase is **not deleted** by this
migration either. It simply stops being read. Orphaning it costs storage;
destroying live data as a side effect of removing a feature is a risk with no
upside. Delete it by hand in Studio after confirming nothing misses it.

### Tightening phase 1

Phase 1 deferred `NOT NULL` and foreign keys on `chatbot_id` until its backfill
was verified. It has been, and this is the natural place:

```sql
alter table public.agents   alter column chatbot_id set not null;
alter table public.sessions alter column chatbot_id set not null;
alter table public.agents   add constraint agents_chatbot_fk
  foreign key (chatbot_id) references public.chatbots (id);
alter table public.sessions add constraint sessions_chatbot_fk
  foreign key (chatbot_id) references public.chatbots (id);
```

This must run **after** the phase 1 post-deploy sweep, not before: any row still
carrying a null `chatbot_id` makes it fail. That failure is the desired
behaviour — it is louder than a silently invisible chat.

## Retrieval

`kb_ids_for` loses two parameters and gains one:

```python
def kb_ids_for(agent_row, session_row, chatbot_kb_ids=None, scratch_kb_id=None)
```

Order is unchanged in shape: the agent's permanent KBs, then the chatbot's
knowledge, then this chat's legacy KB, then this chat's scratch documents as a
`{"id", "source_ids"}` entry. The trailing general-KB branch is deleted.

Two existing rules survive verbatim, because they are the ones that make
isolation work:

- a scratch entry with empty `source_ids` is **dropped, never widened** —
  emitting the shared scratch KB bare would make every other chat's uploads
  answerable here;
- with `agent_row=None` the general assistant never sees a specialist's
  permanent KBs. It now sees chatbot knowledge and scratch, where before it saw
  personal knowledge, general knowledge and scratch.

## Services

`UserKbService` becomes `ChatbotKbService` in `app/services/chatbot_kb.py`,
keyed on a chatbot row rather than a user row. Its four methods keep their
shape: `kb_ids`, `ensure_kb`, `documents`, `untrain`. `untrain` keeps its
comment about never deleting the Source itself — that is now more load-bearing,
not less, because promotion makes multi-KB sources routine.

`general_kb.py` is deleted, along with `ensure_general_kb` at startup and the
`general_kb_id` application state it populated.

`ChatbotService.delete` also deletes the chatbot's two knowledge bases,
best-effort in the same style as the existing agent and chat cleanup, so a
stale resource never blocks the delete.

## Promoting a chat upload

A chat's uploads stay temporary — they die with the chat, as they do today.
What is new is a way out:

```
POST /sessions/{session_id}/documents/{source_id}/promote  ->  202
```

The handler resolves the chatbot from the **session row**, never from the
request body, matching how `/chat` resolves its roster. It 404s unless the
caller owns the session and `source_id` appears in that session's `source_ids`.

Then, in a background task following the existing `_finish_training` pattern:

1. `full_document = 0 < char_count(source_id) <= full_document_max_chars` —
   the same tier rule ingestion already uses, computed from the source that is
   already uploaded;
2. `kb_id = chatbot_kb.ensure_kb(chatbot_row, full_document)`;
3. `add_source_to_kb(kb_id, source_id)`;
4. **remove `source_id` from the session's `source_ids`.**

Step 4 makes this a move rather than a copy. Left in both places, the promoting
chat would search the same document twice — once through scratch and once
through chatbot knowledge — for no benefit. The chat loses nothing, because it
reads chatbot knowledge too.

If the source is already linked to that KB the operation is a success, not an
error: Powabase deduplicates identical content, so promoting the same file
twice is a thing users will do.

Status is polled through the existing `GET /documents/{source_id}/status`.

## Routes

Changed: every endpoint in `knowledge.py` — `POST /train`,
`GET /documents`, `DELETE /documents/{source_id}`,
`GET /documents/{source_id}/status` — takes a `chatbot_id` and verifies
ownership before doing anything, matching phase 1's pattern.

New: the promote endpoint above.

Removed: `POST /admin/train`. Admin keeps user management; there is no
cross-user knowledge left to curate.

Unchanged: every ownership check, and the `404`-not-`403` convention that keeps
a foreign id indistinguishable from a missing one.

## UI

The "My knowledge" panel becomes the current chatbot's knowledge, and its
contents change when the chatbot picker changes. Its description stops
promising that every agent the *user* owns can draw on it, and says the
chatbot's agents instead.

The agent form loses the "Also use shared general knowledge" checkbox. The
admin page loses its training form.

Each uploaded document in a chat gains a **Save to chatbot knowledge** action.

Deleting a chatbot now confirms with counts rather than a bare warning:

> Delete **Work**? This removes 4 agents, 12 chats, and 7 documents.
> This can't be undone.

The document count is the chatbot's own knowledge only, not documents trained
into its agents. Those are counted by the agent deletion that already exists,
and double-counting them here would overstate the damage.

## Testing

Unit, following existing patterns:

- `kb_ids_for` — chatbot knowledge appears for a specialist and for the general
  assistant; no general KB entry is ever emitted; an empty `source_ids` still
  drops the scratch entry rather than widening it.
- `ChatbotKbService` — lazy creation per tier, `documents` across both tiers,
  `untrain` unlinking without deleting the Source.
- Promote — moves the source (present in the chatbot KB, absent from the
  session's `source_ids`), picks the tier by char count, is idempotent, and
  404s for a source that is not in that session.
- `ChatbotService.delete` removes both KBs, and survives one that is already
  gone.
- Knowledge routes 404 for another user's chatbot.
- Two chatbots owned by the same user do not see each other's documents.

Live, after deploying:

- Existing chats answer the eyewash question exactly as before.
- A second chatbot trained on one document answers from it, and the first
  chatbot cannot see that document.
- An agent that had `use_general_kb` ticked answers no differently.
- Promote a chat upload, delete the chat, confirm the document is still in
  chatbot knowledge and still answerable.

## Verification

A script in the phase 1 style, run after applying the migration:

- **no two chatbots share a `kb_id` or a `kb_full_id`** — the one result that
  must never appear;
- every user who had a personal `kb_id` has exactly one chatbot carrying it;
- per-chatbot document counts match the owner's personal document count
  recorded before the migration;
- zero rows with a null `chatbot_id` in `agents` or `sessions`, re-checked
  immediately before the `NOT NULL` tightening.

Capture the per-user personal document counts **before** applying, since the
migration is what makes them per-chatbot.

## Risks

**Two chatbots sharing a KB id.** The failure this phase exists to prevent,
introduced by the migration if the oldest-chatbot guard is dropped. Mechanically
checkable, and checked.

**Ordering.** The `NOT NULL` tightening must follow the phase 1 post-deploy
sweep. Applied early it fails loudly, which is the safe direction.

**Losing the general KB's contents.** Anything an admin uploaded there stops
being answerable the moment this deploys. The KB is left intact in Powabase, so
recovery means re-uploading those files into a chatbot rather than recovering
data.

## Out of scope

- **Moving agents or documents between chatbots.** Create them where you want
  them.
- **Sharing a chatbot** with another account. Chatbots stay private to their
  owner.
- **Dropping the disused columns** and the orphaned general KB. Later, by hand.
