-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  username text not null,
  password_hash text not null,
  created_at timestamptz not null default now()
);
create unique index if not exists users_username_unique on public.users (lower(username));
alter table public.users enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.

alter table public.sessions add column if not exists owner_id uuid;
create index if not exists sessions_owner_updated_idx
  on public.sessions (owner_id, updated_at desc);
