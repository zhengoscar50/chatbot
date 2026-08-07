-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- DESTRUCTIVE. Drops every existing session. Approved in the design spec
-- (docs/superpowers/specs/2026-08-06-user-owned-agents-design.md): existing
-- data is test material and is wiped rather than migrated.

create table if not exists public.agents (
  id                uuid primary key default gen_random_uuid(),
  owner_id          uuid not null,
  name              text not null,
  instructions      text not null default '',
  model             text not null,
  grounding         text not null default 'strict',
  use_general_kb    boolean not null default false,
  powabase_agent_id text not null,
  kb_id             text,
  kb_full_id        text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index if not exists agents_owner_updated_idx
  on public.agents (owner_id, updated_at desc);
alter table public.agents enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.

-- sessions is dropped and recreated rather than altered, because agent_id
-- changes meaning: it held a Powabase agent id and now holds a foreign key
-- into public.agents. kb_full_id is removed (chat scratch is chunk-only, the
-- chunk/full split is reserved for an agent's permanent tier) and user_slug is
-- vestigial now that owner_id exists.
drop table if exists public.sessions;

create table public.sessions (
  id                  uuid primary key default gen_random_uuid(),
  owner_id            uuid not null,
  agent_id            uuid not null references public.agents (id),
  name                text not null,
  kb_id               text,               -- chat scratch KB, created lazily
  powabase_session_id text,               -- the conversation thread
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create index if not exists sessions_owner_updated_idx
  on public.sessions (owner_id, updated_at desc);
create index if not exists sessions_agent_idx
  on public.sessions (agent_id);
alter table public.sessions enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.
