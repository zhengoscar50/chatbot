-- backend/migrations/014_chatbot_sharing.sql
-- Run once in the Powabase Studio SQL Editor.
--
-- Sharing: a chatbot gets an unlisted token and a per-day message cap.
--
-- share_token IS the "is it shared" state — null means not shared. There is
-- deliberately no separate boolean, because two fields describing one fact
-- eventually disagree.
--
-- TEXT, not uuid: the token is secrets.token_urlsafe output, not a uuid. Every
-- id column in this schema that holds an opaque string is already text
-- (sessions.kb_id in 001, agents.kb_id in 004, chatbots.kb_id in 012).
--
-- share_used_date sits beside share_used_today so "resets at midnight" needs
-- no scheduled job: a request arriving on a new date resets the counter in the
-- same write that increments it.

alter table public.chatbots add column if not exists share_token       text;
alter table public.chatbots add column if not exists share_daily_limit int  not null default 100;
alter table public.chatbots add column if not exists share_used_today  int  not null default 0;
alter table public.chatbots add column if not exists share_used_date   date;

-- Partial: many chatbots may have no token, but two may never share one.
create unique index if not exists chatbots_share_token_idx
  on public.chatbots (share_token) where share_token is not null;

-- Marks a chat as belonging to a visitor rather than the owner. Does two jobs:
-- keeps visitor chats out of the owner's sidebar, and proves a session is a
-- visitor's when a public request names it.
alter table public.sessions add column if not exists shared boolean not null default false;
