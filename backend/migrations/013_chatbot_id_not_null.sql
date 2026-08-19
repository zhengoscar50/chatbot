-- backend/migrations/013_chatbot_id_not_null.sql
-- Run once in the Powabase Studio SQL Editor, AFTER the phase 1 post-deploy
-- sweep has stamped every row and verify_chatbot_backfill.py prints PASS.
--
-- Phase 1 left chatbot_id nullable so the then-current release kept working the
-- moment the column appeared. The backfill is verified, so an unstamped row is
-- now a bug rather than a migration state — and a row with no chatbot is a chat
-- nobody can ever see again.
--
-- If either statement fails, a row is still unstamped. That failure is the
-- desired behaviour: louder than a silently invisible chat. Re-run the phase 1
-- sweep and try again.

alter table public.agents   alter column chatbot_id set not null;
alter table public.sessions alter column chatbot_id set not null;

alter table public.agents   add constraint agents_chatbot_fk
  foreign key (chatbot_id) references public.chatbots (id);
alter table public.sessions add constraint sessions_chatbot_fk
  foreign key (chatbot_id) references public.chatbots (id);
