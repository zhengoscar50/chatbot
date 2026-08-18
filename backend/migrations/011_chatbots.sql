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

-- One chatbot per EXISTING user, seeded from public.users rather than from
-- owners of agents/sessions: a user who owns no agents and no chats yet
-- would be skipped by the latter, land with an empty chatbot list, and then
-- have the UI send chatbot_id=null on every request (404s on agent/chat
-- lists, 422 starting a chat) even though they could simply start typing
-- before this migration. Seeding from users covers that idle-but-real
-- account too. Idempotent: re-running cannot create a second chatbot for
-- the same user (guarded by the "not exists" below).
insert into public.chatbots (owner_id, name, description)
select u.id, 'My chatbot',
       'Everything that existed before chatbots were introduced.'
from public.users u
where not exists (
  select 1 from public.chatbots c where c.owner_id = u.id
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

-- ============================================================================
-- ACTION REQUIRED AFTER DEPLOY -- DO NOT SKIP
-- ============================================================================
-- This migration runs BEFORE the new code is deployed. In the window between
-- applying it and deploying, the OLD release is still live and keeps creating
-- agents/sessions the old way: chatbot_id null. Those rows are stamped by
-- neither backfill above (they didn't exist yet when this ran), so they stay
-- null forever -- invisible to GET /sessions, and POST /chat on one of them
-- calls agents.list(None), which PostgREST rejects with a 400.
--
-- Once the new code is deployed, RE-RUN the two UPDATE statements above
-- (they are idempotent -- "chatbot_id is null" only touches rows still
-- missing one) to sweep up anything the old release created during the gap.
-- Then re-run, from backend/:
--
--   python -m scripts.verify_chatbot_backfill
--
-- to confirm no agent or session row is left with a null chatbot_id.
-- ============================================================================
