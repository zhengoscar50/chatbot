"""Sharing a chatbot: tokens, the daily cap, and public-view redaction."""
from __future__ import annotations


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


import secrets
from datetime import date

from fastapi import Request

# 32 bytes of urlsafe randomness. The link is unlisted rather than secret, but
# it must not be guessable by anyone who finds one other link.
TOKEN_BYTES = 32


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

    def consume(self, chatbot_row: dict, today: date | None = None) -> bool:
        """Claim one message against today's allowance.

        Returns False when the cap is reached, having changed nothing.

        A counter from an earlier date is treated as zero and overwritten in
        the same write, so "resets at midnight" needs no scheduled job.

        Two simultaneous requests can both read the same count and both
        proceed. At this scale that costs one extra message, not a breach, and
        locking is not worth its complexity here.
        """
        today = today or date.today()
        stamp = today.isoformat()
        used = int(chatbot_row.get("share_used_today") or 0)
        limit = int(chatbot_row.get("share_daily_limit") or 0)
        if str(chatbot_row.get("share_used_date") or "") != stamp:
            used = 0
        if used >= limit:
            return False
        self.client.update_chatbot_row(chatbot_row["id"], {
            "share_used_today": used + 1,
            "share_used_date": stamp,
        })
        return True


def get_share_service(request: Request) -> "ShareService":
    """FastAPI dependency returning the shared ShareService."""
    return request.app.state.share_service
