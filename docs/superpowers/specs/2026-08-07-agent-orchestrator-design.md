# Agent Orchestrator — Design Spec

**Date:** 2026-08-07
**Status:** Approved for planning
**Replaces:** one-agent-per-chat binding

## Goal

A chat is no longer bound to a single agent. An orchestrator reads each message,
routes it to whichever of the user's agents is best suited, and relays that
agent's answer. A built-in general assistant handles anything no specialist
covers.

## Context

Today `sessions.agent_id` binds a chat to exactly one agent, so using a
different agent means starting a different chat and knowing in advance which
one you need. The user wants one conversation backed by their whole roster.

## Decisions

1. **Every agent the user owns is a candidate** in every chat. `sessions.agent_id`
   is dropped. Simplest mental model; the cost is that a large roster makes the
   routing prompt longer and picks less reliable. Revisit with a per-chat team
   if that becomes real.

2. **The specialist's answer is relayed verbatim.** The orchestrator routes and
   attributes; it does not rewrite. An extra writing pass would cost a third LLM
   call and — as the deep-research work demonstrated — degrade citations, since
   a rewrite can drop `[n]` markers or reattach them to the wrong claim. Two LLM
   calls per message: orchestrate, then answer.

3. **The retrieval gate is folded into the orchestrator, not added alongside it.**
   One JSON-schema call returns both decisions:
   `{"agent_id": <id|null>, "needs_kb": <bool>}`. `GateService` and
   `router_agent` are **deleted**. Without this the feature would cost three LLM
   calls per message, which is worse than the status quo.

4. **A built-in general assistant** answers when `agent_id` is null. Provisioned
   at startup like the current gate agent. This is what makes the app work on
   first login with zero agents built, and what stops routing from being forced
   into a bad match for "hi".

5. **Agents gain a `description`** — one line written for the router ("Answers
   questions about our AP Chemistry course materials"). Instructions are a
   persona prompt and often say nothing about scope, so routing on them alone
   would depend on how the user happened to word an unrelated field. An explicit
   field means a mis-routing agent is fixed by editing one sentence.

## Object model

```
User ──owns──▶ Agent  (instructions, description, model, grounding, permanent KBs)
User ──owns──▶ Chat   (thread + scratch docs)
```

## Schema (migration 005)

Non-destructive; existing chats keep their history.

```sql
alter table public.agents add column if not exists description text not null default '';
drop index if exists sessions_agent_idx;
alter table public.sessions drop column if exists agent_id;
```

## Orchestration

A shared app-owned agent (`agent-orchestrator`, `router_agent_model`,
temperature 0) answering against a strict JSON schema:

```json
{"agent_id": "<uuid or null>", "needs_kb": true}
```

Input: the user's roster as `(id, name, description)`, the last
`gate_history_turns` (2) turns, and the current message. History matters — a
follow-up like "explain that again" must route to whoever just answered rather
than being re-decided from a fragment.

**Fail-safe:** any error, unparseable output, or an `agent_id` not in the
roster resolves to the general assistant with `needs_kb: true`. A broken router
degrades to a working chatbot, never to a broken one. This mirrors
`GateService`'s existing fail-open rule.

## Retrieval scope

| Answering | In scope |
|---|---|
| A specialist | its `kb_id` + `kb_full_id` + this chat's scratch KB + general KB *if `use_general_kb`* |
| General assistant | this chat's scratch KB + general KB |

`kb_ids_for` grows a branch for the no-specialist case. The general assistant
must **never** see a specialist's permanent KBs — that would leak one agent's
documents into an answer the user attributed to another.

## Message flow

1. Verify chat ownership → 404
2. Load recent turns from the Powabase thread
3. Load the user's agent roster
4. Orchestrate → `{agent_id, needs_kb}`
5. Resolve the answering agent (specialist row, or the general assistant)
6. Compose scope; build a context handler only if `needs_kb` **and** the scope is
   non-empty (an empty scope already skips retrieval — see the untrained-agent fix)
7. Run the answering agent; relay its answer and citations unchanged

## API changes

- `POST /sessions` no longer accepts `agent_id`
- `ChatResponse` gains `answered_by: {id, name}` (id null for the general assistant)
- `AgentCreateRequest` / `AgentUpdateRequest` / `AgentResponse` gain `description`

## UI

- The sidebar agent picker is **removed** — it selected a chat's agent, a concept
  that no longer exists. Replaced by a **Manage agents** button opening a list
  (name, description, trained state), each row opening the existing edit form.
- The chat list becomes flat again.
- Each assistant message carries a badge naming the agent that answered. This is
  the feedback loop for tuning descriptions: mis-routing is only fixable if it's
  visible.
- The agent form gains a description field.

## Testing

- **Routing** — distinct descriptions route correctly; nothing-fits returns null;
  an orchestration error falls back to the general assistant rather than 500ing;
  an `agent_id` outside the roster is rejected (a hallucinated id must not be
  trusted).
- **Scope composition** — the general-assistant case never includes a
  specialist's permanent KBs.
- **Attribution** — the response names the agent that actually ran.

## Out of scope

- Per-chat agent teams
- Multiple agents collaborating on one answer
- The orchestrator rewriting or merging answers
- Auto-generated descriptions

## Risks

- **Routing quality is the whole feature** and depends on user-written
  descriptions. The badge makes mis-routing visible; nothing else guards it.
- **A large roster degrades routing** and lengthens every orchestration prompt.
  No cap is imposed; watch it.
- **A hallucinated agent id** must be validated against the roster before use,
  or the orchestrator could name an agent that isn't the user's.
