-- backend/migrations/012_chatbot_knowledge.sql
-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- Moves knowledge down a level: a chatbot owns its knowledge base, where the
-- user used to. Same two tiers agents and users already have — a short
-- document is indexed whole, a long one is chunked.
--
-- users.kb_id and users.kb_full_id are deliberately LEFT IN PLACE. Dropping a
-- column in the same migration that stops reading it means a rollback loses
-- data. Drop them by hand once this release has been live long enough to
-- trust.

-- TEXT, not uuid: every knowledge-base id column in this schema is text
-- (sessions.kb_id in 001, agents.kb_id in 004, users.kb_id in 008). Powabase
-- knowledge-base ids are opaque strings and are never compared as uuids.
--
-- If you applied an earlier draft of this file that used uuid, the copy below
-- failed with "column kb_id is of type uuid but expression is of type text".
-- Correct it with:
--     alter table public.chatbots alter column kb_id      type text using kb_id::text;
--     alter table public.chatbots alter column kb_full_id type text using kb_full_id::text;
alter table public.chatbots add column if not exists kb_id      text;
alter table public.chatbots add column if not exists kb_full_id text;

-- Copy each user's personal knowledge onto their OLDEST chatbot.
--
-- The subselect is the whole safety argument. "Every user has exactly one
-- chatbot" is true today and stops being true the moment anyone creates a
-- second one. Without it, a user with two chatbots gets the same kb_id written
-- to both — two chatbots reading one knowledge base, which is precisely the
-- leak this migration exists to prevent.
--
-- The `is null` guards make this idempotent: re-running never re-stamps a
-- chatbot whose knowledge has since diverged.
update public.chatbots c
   set kb_id = u.kb_id, kb_full_id = u.kb_full_id
  from public.users u
 where u.id = c.owner_id
   and c.kb_id is null and c.kb_full_id is null
   and c.id = (select c2.id from public.chatbots c2
                where c2.owner_id = u.id
                order by c2.created_at asc
                limit 1);
