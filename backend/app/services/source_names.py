"""Keeping the owner's filenames out of prose a stranger reads.

`redact_turn` remaps a filename to "Source 2" when the model CITED that
document on the same turn. It cannot help when the model names a document
without citing it — the prompts tell every agent to cite its sources, so its
prose talks about documents by name, and nothing in that turn's citations
identifies the name as one to hide.

Closing it needs the one thing that turn does not have: the names of every
document in the chatbot's knowledge. That is what SourceNameIndex holds.

The scrubbing rule is deliberately asymmetric, because the two failures are
not equally bad. Missing a leak is rare and quiet. Corrupting an answer — a
document called Pricing.pdf turning every "pricing" into "a document" —
happens on every turn and is read by the visitor. So a full filename is always
removed, and a bare stem only when it could not plausibly be an ordinary word.
"""
import re
import time

from fastapi import Request

REPLACEMENT = "a document"

# A stem is safe to scrub when it could not be a word in a sentence: it carries
# a digit or a separator, or is simply too long to be one.
_HAS_NON_WORD = re.compile(r"[\d_\-.]")
_LONG_ENOUGH = 12


def is_distinctive_stem(stem: str) -> bool:
    """Whether this filename stem is unmistakable enough to remove from prose.

    "Q3-financials" and "internal_roadmap_2026" could only be filenames.
    "Pricing" and "Summary" are words the answer may legitimately use.
    """
    stem = (stem or "").strip()
    if not stem:
        return False
    return bool(_HAS_NON_WORD.search(stem)) or len(stem) >= _LONG_ENOUGH


def _targets(known_names) -> list:
    """The strings to remove, longest first.

    Longest first matters: "Q3-financials.pdf" must be tried before the stem
    "Q3-financials", or the extension is left stranded in the sentence.
    """
    out = set()
    for name in known_names or []:
        name = str(name or "").strip()
        if not name:
            continue
        out.add(name)
        stem = name.rsplit(".", 1)[0] if "." in name else name
        if stem != name and is_distinctive_stem(stem):
            out.add(stem)
    return sorted(out, key=len, reverse=True)


def redact_names_in_prose(answer: str, known_names) -> str:
    """`answer` with any of the chatbot's document names replaced.

    Matched case-insensitively and only at word boundaries, so "Pricing.pdf"
    does not fire inside "Repricing.pdfs".
    """
    if not answer:
        return ""
    out = answer
    for target in _targets(known_names):
        # \b does not work against a trailing "." in the filename, so the tail
        # boundary is "not a word character or dot" instead.
        pattern = re.compile(
            r"(?<![\w.])" + re.escape(target) + r"(?![\w.])", re.IGNORECASE
        )
        out = pattern.sub(REPLACEMENT, out)
    return out


class SourceNameIndex:
    """Document names per knowledge base, cached.

    Cached by KB id rather than by chatbot because that is the unit the writes
    are addressed to: every document that becomes answerable arrives through
    `add_source_to_kb`, so `invalidate` on that one call is exact, and no
    caller can add a document the index does not then re-read.

    The TTL is a backstop for the one path the app does not own — a document
    added straight through the Powabase dashboard, which no hook here can see.
    """

    TTL_SECONDS = 300.0

    def __init__(self, client, ttl: float = TTL_SECONDS, clock=time.monotonic):
        self.client = client
        self.ttl = ttl
        self._clock = clock
        self._cache: dict = {}

    def invalidate(self, kb_id: str) -> None:
        self._cache.pop(kb_id, None)

    def names_for(self, kb_ids) -> frozenset:
        """Every document name across these knowledge bases."""
        out: set = set()
        for kb_id in kb_ids or []:
            # kb_ids_for yields bare ids AND {"id", "source_ids"} dicts; a
            # scratch entry is a visitor's own upload, so its name is theirs to
            # see and it is skipped rather than looked up.
            if not isinstance(kb_id, str):
                continue
            out |= self._names_for_one(kb_id)
        return frozenset(out)

    def _names_for_one(self, kb_id: str) -> frozenset:
        hit = self._cache.get(kb_id)
        if hit is not None and (self._clock() - hit[0]) < self.ttl:
            return hit[1]
        try:
            items = self.client.list_kb_sources(kb_id).get("items", [])
        except Exception:
            # A lookup failure must not take the answer down with it. The turn
            # proceeds with whatever other names are known; the citation-based
            # redaction in redact_turn is unaffected either way.
            return frozenset()
        names = frozenset(
            str(i.get("source_name")) for i in items if i.get("source_name")
        )
        self._cache[kb_id] = (self._clock(), names)
        return names


def knowledge_kb_ids(chatbot_kb_ids, agent_rows) -> list:
    """Every knowledge base whose document names an answer could name.

    Both tiers, because either can supply the context a model then talks
    about: the chatbot's own knowledge, and each specialist's permanent KBs.
    A chat's scratch uploads are deliberately absent — those are the visitor's
    own documents.
    """
    ids = list(chatbot_kb_ids or [])
    for row in agent_rows or []:
        ids.extend([row.get("kb_id"), row.get("kb_full_id")])
    return [i for i in dict.fromkeys(ids) if i]


def get_source_names(request: Request) -> "SourceNameIndex":
    """FastAPI dependency returning the shared SourceNameIndex."""
    return request.app.state.source_names
