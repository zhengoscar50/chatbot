-- backend/migrations/011_chatbots.sql
-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- Adds a layer between a user and their agents: a user owns chatbots, a
-- chatbot owns agents and chats.
--
-- chatbot_id is NULLABLE and carries no foreign key on purpose. A NOT NULL FK
-- would fail on existing rows, and nullable means the currently deployed
-- release keeps working the moment this is applied. Tightening is a follow-up
-- once the backfill is verified.

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

-- One chatbot per user who already owns anything. Idempotent: re-running
-- cannot create a second chatbot for the same user.
insert into public.chatbots (owner_id, name, description)
select distinct owners.owner_id, 'My chatbot',
       'Everything that existed before chatbots were introduced.'
from (
  select owner_id from public.agents
  union
  select owner_id from public.sessions
) owners
where not exists (
  select 1 from public.chatbots c where c.owner_id = owners.owner_id
);

-- Stamp existing rows. Guarded by "is null" so a re-run never moves a row
-- that has since been reassigned.
update public.agents a
   set chatbot_id = c.id
  from public.chatbots c
 where c.owner_id = a.owner_id and a.chatbot_id is null;

update public.sessions s
   set chatbot_id = c.id
  from public.chatbots c
 where c.owner_id = s.owner_id and s.chatbot_id is null;
