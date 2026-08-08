from __future__ import annotations


def conversation_message(history: list, query: str) -> str:
    """The current question, with recent turns inline above it.

    Agents run statelessly — a Powabase thread belongs to exactly one agent, so
    a chat several agents take turns in cannot use one. Carrying the history in
    the message is what lets a specialist answer a follow-up about something a
    different agent said.
    """
    if not history:
        return query
    lines = ["Recent conversation:"]
    for turn in history:
        lines.append("%s: %s" % (turn.get("role", "user"), turn.get("text", "")))
    lines.append("")
    lines.append("Current message: %s" % query)
    return "\n".join(lines)
