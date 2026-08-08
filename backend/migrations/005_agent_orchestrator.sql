-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- Non-destructive: existing chats keep their history. sessions.agent_id is
-- dropped because a chat is no longer bound to one agent — the orchestrator
-- picks per message from the whole roster. Dropping the column also drops its
-- index and foreign key.

alter table public.agents
  add column if not exists description text not null default '';

drop index if exists sessions_agent_idx;
alter table public.sessions drop column if exists agent_id;
