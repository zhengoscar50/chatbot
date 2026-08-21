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
    id_to_label: dict = {}  # Maps source_id to label
    name_to_id: dict = {}   # Maps source_name to source_id when encountered
    out: list = []
    for citation in citations or []:
        if not isinstance(citation, dict):
            # The legacy citation shape is the filename itself. There is
            # nothing to redact, so drop it: a missing marker beats a leak.
            continue

        source_id = citation.get("source_id")
        source_name = citation.get("source_name")

        # Coerce to string for hashability (in case of nested/unhashable values)
        if source_id is not None:
            source_id = str(source_id)
        if source_name is not None:
            source_name = str(source_name)

        # Determine the canonical identity, preferring source_id (the real key)
        # but remembering when source_name is associated with a source_id
        if source_id:
            identity = source_id
            # Remember this source_name is tied to this source_id
            if source_name:
                name_to_id[source_name] = source_id
        elif source_name:
            # Check if we've seen this source_name before with a source_id
            if source_name in name_to_id:
                identity = name_to_id[source_name]
            else:
                identity = source_name
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
