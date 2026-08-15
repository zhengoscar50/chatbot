-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- Non-destructive, and safe to apply while the old code is running: both
-- columns default to NULL, which the previous release simply ignores.
--
-- A personal knowledge base per user. Trained once, searched by every agent
-- that user owns — including the general assistant, since this is the user's
-- own knowledge rather than any one agent's.
--
-- Two columns for the same reason agents have two: a short document is
-- indexed whole (full_document strategy) and a long one is chunked, so a
-- one-page note keeps whole-document retrieval quality.
--
-- Both are created lazily, so a user who never trains costs no knowledge base.
--
-- Deliberately one KB per user rather than a single shared KB filtered by
-- source_ids: filter-based isolation is what broke when deleting a chat
-- unlinked a source another chat still referenced. Users are few; structural
-- isolation cannot leak.

alter table public.users
  add column if not exists kb_id text;

alter table public.users
  add column if not exists kb_full_id text;
