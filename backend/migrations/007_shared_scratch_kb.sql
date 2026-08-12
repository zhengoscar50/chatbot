-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- Non-destructive, and safe to apply while the old code is running: the
-- column defaults to an empty list, which the previous release simply ignores.
--
-- Chat scratch uploads used to get a whole Powabase Knowledge Base each
-- (`chat-<session_id>-kb`, held in sessions.kb_id). One KB per chat is a lot
-- of infrastructure for a handful of throwaway documents. Powabase's
-- query-specific attachment takes `source_ids`, which restricts retrieval to
-- named documents inside a KB, so one shared scratch KB can serve every chat:
-- a chat searches only the sources listed here.
--
-- sessions.kb_id is deliberately NOT dropped. Chats created before this keep
-- their own KB and are still searched through it (see retrieval_scope), so no
-- live chat loses its uploads and nothing needs backfilling.

alter table public.sessions
  add column if not exists source_ids jsonb not null default '[]'::jsonb;
