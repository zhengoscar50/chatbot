"""Sharing a chatbot: tokens, the daily cap, and public-view redaction."""
from __future__ import annotations

import secrets
from datetime import date

from app.services.source_names import redact_names_in_prose

from fastapi import Request

# 32 bytes of urlsafe randomness. The link is unlisted rather than secret, but
# it must not be guessable by anyone who finds one other link.
TOKEN_BYTES = 32


def redact_citations(citations: list) -> list:
    """Citations as a stranger may see them: markers and excerpts, no filename.

    The excerpt is what makes an answer credible — proof it came from a
    document rather than the model's memory. The filename carries almost none
    of that value and all of the exposure: anyone with the link would otherwise
    learn the name of every document in the chatbot's knowledge, including ones
    no answer of theirs ever touched.

    `source_id` is dropped too, not just the name. The frontend falls back to
    `source_id` when a name is absent, so keeping it would print a raw
    identifier in exactly the place the filename used to be.

    Labels are assigned per distinct source, so two markers quoting the same
    document agree — which is what the citation de-duplication in the UI needs.
    """
    # First pass: learn which source_names map to which source_ids.
    # This handles both order cases (id-first and name-first).
    name_to_id: dict = {}
    for citation in citations or []:
        if not isinstance(citation, dict):
            continue
        source_id = citation.get("source_id")
        source_name = citation.get("source_name")
        # Coerce to string for hashability
        if source_id is not None:
            source_id = str(source_id)
        if source_name is not None:
            source_name = str(source_name)
        # Record the mapping only if we have both
        if source_id and source_name:
            name_to_id[source_name] = source_id

    # Second pass: assign labels using resolved identities.
    id_to_label: dict = {}
    out: list = []
    for citation in citations or []:
        if not isinstance(citation, dict):
            # The legacy citation shape is the filename itself. There is
            # nothing to redact, so drop it: a missing marker beats a leak.
            continue

        source_id = citation.get("source_id")
        source_name = citation.get("source_name")

        # Coerce to string for hashability
        if source_id is not None:
            source_id = str(source_id)
        if source_name is not None:
            source_name = str(source_name)

        # Determine identity: own source_id if present, else resolve through name_to_id
        if source_id:
            identity = source_id
        elif source_name:
            # Use the source_id we learned if this name has one, else use the name
            identity = name_to_id.get(source_name, source_name)
        else:
            # No identity at all
            continue

        # Assign label if not seen before
        if identity not in id_to_label:
            id_to_label[identity] = "Source %d" % (len(id_to_label) + 1)

        out.append({
            "key": citation.get("key"),
            "source_name": id_to_label[identity],
            "text_excerpt": citation.get("text_excerpt") or "",
        })
    return out


def redact_turn(answer: str, citations: list, known_names=()) -> tuple:
    """Answer and citations as a stranger may see them, redacted together.

    They share one label map on purpose: if the prose says "Q3.pdf" and the
    citation list calls that document "Source 2", the answer must say
    "Source 2" as well, or the two disagree in front of the visitor.

    `known_names` closes the case labels cannot: a model naming a document it
    did NOT cite this turn. Nothing in that turn identifies such a name as one
    to hide, so the chatbot's own document names are passed in from
    SourceNameIndex and scrubbed from the prose afterwards.
    """
    redacted_citations = redact_citations(citations)
    if not redacted_citations:
        # No labels to apply, but the prose may still name a document.
        return redact_names_in_prose(answer, known_names) if isinstance(answer, str) \
            else answer, redacted_citations

    # Build the same identity->label map redact_citations just used, so the
    # answer's replacements agree with the citation list's labels.
    label_by_name: dict = {}
    for original, redacted in zip(citations or [], redacted_citations):
        if not isinstance(original, dict):
            continue
        source_name = original.get("source_name")
        if source_name:
            label_by_name[str(source_name)] = redacted["source_name"]

    if not isinstance(answer, str):
        return answer, redacted_citations

    # Longest name first: "report.pdf" inside "annual_report.pdf" must not
    # corrupt the longer name by replacing its suffix first.
    redacted_answer = answer
    for name in sorted(label_by_name, key=len, reverse=True):
        redacted_answer = redacted_answer.replace(name, label_by_name[name])

    # Then anything the labels could not reach. This runs even when the turn
    # cited nothing at all — an answer with no citations is exactly where an
    # uncited filename hides.
    return redact_names_in_prose(redacted_answer, known_names), redacted_citations


class ShareService:
    """An unlisted link to one chatbot, with a per-day message cap.

    The cap is this feature's load-bearing safety property: there is no rate
    limiting anywhere else in the application, and a public route bypasses both
    authentication and ownership. Every anonymous message spends the owner's
    credits.
    """

    def __init__(self, client):
        self.client = client

    def enable(self, chatbot_id: str) -> str:
        """Create or REPLACE the token, returning the new one.

        Replacing is how revoke-and-reissue works: the previous link stops
        resolving the moment this returns.
        """
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self.client.update_chatbot_row(chatbot_id, {"share_token": token})
        return token

    def disable(self, chatbot_id: str) -> None:
        self.client.update_chatbot_row(chatbot_id, {"share_token": None})

    def resolve(self, token: str):
        """The chatbot this token belongs to, or None."""
        if not token:
            return None
        return self.client.get_chatbot_by_share_token(token)

    @staticmethod
    def _usage(chatbot_row: dict, today: date) -> tuple[int, int]:
        """(used, limit) for `today`.

        A counter stamped with an earlier date is treated as zero — this is
        the whole of "resets at midnight"; no scheduled job needed.
        """
        used = int(chatbot_row.get("share_used_today") or 0)
        limit = int(chatbot_row.get("share_daily_limit") or 0)
        if str(chatbot_row.get("share_used_date") or "") != today.isoformat():
            used = 0
        return used, limit

    def has_room(self, chatbot_row: dict, today: date | None = None) -> bool:
        """Whether one more message would fit inside today's allowance.

        Applies the same date-reset logic as `consume` but writes nothing —
        safe for a caller that only wants to check before doing anything
        stateful, such as refusing to create a session once the cap is spent.
        """
        used, limit = self._usage(chatbot_row, today or date.today())
        return used < limit

    def consume(self, chatbot_row: dict, today: date | None = None) -> bool:
        """Claim one message against today's allowance.

        Returns False when the cap is reached, having changed nothing.
        Asks `has_room` for the cap check so the two can never drift apart.

        Two simultaneous requests can both read the same count and both
        proceed. At this scale that costs one extra message, not a breach, and
        locking is not worth its complexity here.
        """
        today = today or date.today()
        if not self.has_room(chatbot_row, today):
            return False
        used, _ = self._usage(chatbot_row, today)
        self.client.update_chatbot_row(chatbot_row["id"], {
            "share_used_today": used + 1,
            "share_used_date": today.isoformat(),
        })
        return True


def get_share_service(request: Request) -> "ShareService":
    """FastAPI dependency returning the shared ShareService."""
    return request.app.state.share_service
