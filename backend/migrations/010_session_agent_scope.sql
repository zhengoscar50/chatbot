-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- Non-destructive, and safe to apply while the old code is running: the column
-- defaults to an empty list, which the previous release ignores.
--
-- Which of the user's agents are kept OUT of one chat. Every chat starts with
-- the whole roster; this narrows it.
--
-- Exclusion rather than inclusion, deliberately. With a list of the agents that
-- ARE allowed, an agent created tomorrow would be silently missing from every
-- chat made before it existed — the opposite of "all agents by default". An
-- exclusion list means new agents join everywhere automatically, and only a
-- deliberate choice keeps one out.
--
-- Excluding every agent is a legitimate state, not an error: it is how a chat
-- says "just the general assistant". The orchestrator already returns the
-- general assistant for an empty roster without paying for a routing call.

alter table public.sessions
  add column if not exists excluded_agent_ids jsonb not null default '[]'::jsonb;
