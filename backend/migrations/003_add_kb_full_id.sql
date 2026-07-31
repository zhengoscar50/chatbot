-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
alter table public.sessions add column if not exists kb_full_id text;
