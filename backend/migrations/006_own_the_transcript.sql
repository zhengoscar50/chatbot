-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- A Powabase conversation thread belongs to exactly one agent
-- (ai.agent_sessions.session_id is UNIQUE), so one chat cannot be shared
-- across agents. The app stores its own transcript instead and runs agents
-- statelessly, passing recent turns inline.
--
-- DESTRUCTIVE for chat history: transcripts lived in Powabase threads, which
-- this abandons. Chats, agents and their training are untouched.

create table if not exists public.messages (
  id               uuid primary key default gen_random_uuid(),
  session_id       uuid not null references public.sessions (id) on delete cascade,
  role             text not null,               -- 'user' | 'assistant'
  content          text not null,
  citations        jsonb not null default '[]'::jsonb,
  answered_by_id   uuid,                        -- null for user turns and the general assistant
  answered_by_name text,
  created_at       timestamptz not null default now()
);
create index if not exists messages_session_created_idx
  on public.messages (session_id, created_at);
alter table public.messages enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.

-- Dead now that threads are gone.
alter table public.sessions drop column if exists powabase_session_id;
