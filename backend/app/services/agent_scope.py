from __future__ import annotations


def roster_for(roster: list, excluded_agent_ids) -> list:
    """The agents the orchestrator may choose from for one chat.

    Every chat starts with the whole roster; a chat narrows it by naming the
    agents to keep OUT. Exclusion rather than inclusion is deliberate — an
    agent created tomorrow joins every existing chat automatically, which is
    what "all agents by default" has to mean.

    A stale id (an agent deleted while a chat still lists it) matches nothing
    and is harmless. An empty result is legitimate: it is how a chat says
    "just the general assistant".
    """
    excluded = set(excluded_agent_ids or [])
    if not excluded:
        return roster
    return [agent for agent in roster if agent.get("id") not in excluded]


def sanitise_exclusions(requested, roster: list) -> list:
    """What may actually be stored: ids from this user's own roster, deduped.

    A client can post anything, so the set is intersected with the roster
    rather than trusted. Ordering follows the roster so the same selection
    always serialises identically and a no-op save does not look like a change.

    Doubles as the pruner: passing the current roster drops ids whose agents
    have since been deleted.
    """
    if not isinstance(requested, list):
        return []
    wanted = {value for value in requested if isinstance(value, str)}
    return [agent["id"] for agent in roster if agent.get("id") in wanted]
