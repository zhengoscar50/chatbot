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
    labels: dict = {}
    out: list = []
    for citation in citations or []:
        if not isinstance(citation, dict):
            # The legacy citation shape is the filename itself. There is
            # nothing to redact, so drop it: a missing marker beats a leak.
            continue
        identity = citation.get("source_id") or citation.get("source_name")
        if identity not in labels:
            labels[identity] = "Source %d" % (len(labels) + 1)
        out.append({
            "key": citation.get("key"),
            "source_name": labels[identity],
            "text_excerpt": citation.get("text_excerpt") or "",
        })
    return out
