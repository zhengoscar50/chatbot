-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- Non-destructive, and safe to apply while the old code is running: the column
-- defaults to NULL, which the previous release ignores.
--
-- How many tokens of retrieved document text one agent may be given per
-- question. NULL means "use the default", so existing agents keep working
-- without a backfill.
--
-- The value is always clamped server-side to half the answering model's
-- context window: that window is shared with the system prompt, the inlined
-- conversation history and the answer itself, so spending all of it on
-- retrieval would overflow. Halving it means a maxed-out setting cannot fail.
--
-- Changing an agent's model re-clamps this down when the new model's window is
-- smaller, so a value legal for a 200k model cannot survive a move to a 128k
-- one.

alter table public.agents
  add column if not exists max_context_tokens integer;
