-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
create table if not exists public.sessions (
  id uuid primary key default gen_random_uuid(),
  user_slug text not null,
  name text not null,
  kb_id text not null,
  agent_id text not null,
  powabase_session_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists sessions_user_updated_idx
  on public.sessions (user_slug, updated_at desc);
alter table public.sessions enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.
