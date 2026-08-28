from types import SimpleNamespace

from app.services.source_names import (
    is_distinctive_stem,
    redact_names_in_prose,
)


# --- what the scrubber must catch -----------------------------------------

def test_a_filename_the_model_named_without_citing_is_removed():
    """The leak this exists for. The prompts tell every agent to cite its
    sources, so its prose names documents; redact_turn only remaps names it saw
    in THIS turn's citations, and a model that mentions a document without
    citing it walks straight past that."""
    out = redact_names_in_prose(
        "According to Q3-financials.pdf, revenue rose.", {"Q3-financials.pdf"}
    )

    assert "Q3-financials" not in out
    assert "a document" in out


def test_the_match_ignores_case():
    out = redact_names_in_prose("See q3-FINANCIALS.PDF for detail.",
                                {"Q3-financials.pdf"})

    assert "q3-FINANCIALS" not in out.replace("a document", "")


def test_a_distinctive_stem_without_its_extension_is_caught():
    """Models routinely drop the extension — "the Q3-financials document"."""
    out = redact_names_in_prose("The Q3-financials document says so.",
                                {"Q3-financials.pdf"})

    assert "Q3-financials" not in out


def test_every_document_named_is_removed_not_just_the_first():
    out = redact_names_in_prose(
        "Both internal_roadmap_2026.docx and Q3-financials.pdf agree.",
        {"internal_roadmap_2026.docx", "Q3-financials.pdf"},
    )

    assert "roadmap" not in out and "financials" not in out


# --- what it must NOT touch ------------------------------------------------

def test_an_ordinary_word_that_happens_to_be_a_filename_survives():
    """A document called Pricing.pdf must not turn every use of the word
    "pricing" into "a document". Corrupting real answers is a worse failure
    than the leak: it happens on every turn instead of rarely, and it is
    visible to the visitor."""
    out = redact_names_in_prose(
        "Our pricing depends on volume.", {"Pricing.pdf"}
    )

    assert out == "Our pricing depends on volume."


def test_the_full_filename_is_still_caught_even_when_its_stem_is_a_common_word():
    """The stem is ambiguous; "Pricing.pdf" is not. The extension is what makes
    it unmistakably a filename rather than a word in a sentence."""
    out = redact_names_in_prose("See Pricing.pdf for detail.", {"Pricing.pdf"})

    assert "Pricing.pdf" not in out
    assert "a document" in out


def test_a_name_appearing_inside_a_longer_word_is_left_alone():
    out = redact_names_in_prose("Repricing.pdfs are elsewhere.", {"Pricing.pdf"})

    assert out == "Repricing.pdfs are elsewhere."


def test_prose_is_untouched_when_the_chatbot_has_no_documents():
    text = "Revenue rose in the third quarter."

    assert redact_names_in_prose(text, set()) == text


def test_an_empty_answer_is_handled():
    assert redact_names_in_prose("", {"a.pdf"}) == ""
    assert redact_names_in_prose(None, {"a.pdf"}) == ""


# --- the rule that decides which stems are safe to scrub -------------------

def test_stems_that_could_be_ordinary_words_are_not_distinctive():
    for word in ["Pricing", "Report", "Notes", "Summary", "faq"]:
        assert not is_distinctive_stem(word), word


def test_stems_that_could_not_be_prose_are_distinctive():
    for name in ["Q3-financials", "internal_roadmap_2026", "2026budget",
                 "acme-master-services-agreement"]:
        assert is_distinctive_stem(name), name


# --- the index and its cache ----------------------------------------------

from app.services.source_names import SourceNameIndex


class FakeKbClient:
    def __init__(self, by_kb=None):
        self.by_kb = dict(by_kb or {})
        self.calls = []

    def list_kb_sources(self, kb_id):
        self.calls.append(kb_id)
        return {"items": [{"source_name": n} for n in self.by_kb.get(kb_id, [])]}


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def test_names_are_collected_across_every_knowledge_base():
    c = FakeKbClient({"kb-agent": ["a.pdf"], "kb-bot": ["b.pdf"]})

    assert SourceNameIndex(c).names_for(["kb-agent", "kb-bot"]) == {"a.pdf", "b.pdf"}


def test_a_second_lookup_does_not_hit_the_api_again():
    """This runs on the answer path of every visitor message. Without the
    cache it adds a round trip per knowledge base to every turn."""
    c = FakeKbClient({"kb": ["a.pdf"]})
    idx = SourceNameIndex(c)

    idx.names_for(["kb"])
    idx.names_for(["kb"])

    assert c.calls == ["kb"]


def test_adding_a_document_makes_the_next_lookup_see_it():
    """The window this design exists to close. A TTL alone would leave a
    document unprotected between its upload and the cache expiring."""
    c = FakeKbClient({"kb": ["old.pdf"]})
    idx = SourceNameIndex(c)
    idx.names_for(["kb"])

    c.by_kb["kb"] = ["old.pdf", "brand-new.pdf"]
    idx.invalidate("kb")

    assert "brand-new.pdf" in idx.names_for(["kb"])


def test_without_invalidation_the_ttl_still_catches_up():
    """The backstop for the path the app does not own: a document added
    straight through the Powabase dashboard fires no hook here."""
    clock = Clock()
    c = FakeKbClient({"kb": ["old.pdf"]})
    idx = SourceNameIndex(c, ttl=300.0, clock=clock)
    idx.names_for(["kb"])

    c.by_kb["kb"] = ["old.pdf", "added-elsewhere.pdf"]
    assert "added-elsewhere.pdf" not in idx.names_for(["kb"])   # still cached

    clock.t += 301.0
    assert "added-elsewhere.pdf" in idx.names_for(["kb"])


def test_invalidating_one_knowledge_base_leaves_the_others_cached():
    c = FakeKbClient({"kb1": ["a.pdf"], "kb2": ["b.pdf"]})
    idx = SourceNameIndex(c)
    idx.names_for(["kb1", "kb2"])

    idx.invalidate("kb1")
    idx.names_for(["kb1", "kb2"])

    assert c.calls == ["kb1", "kb2", "kb1"]


def test_a_scratch_entry_is_skipped_rather_than_looked_up():
    """kb_ids_for yields {"id", "source_ids"} dicts for a chat's own uploads.
    Those are the visitor's OWN documents — their name is theirs to see, and
    hiding it from them would be nonsense."""
    c = FakeKbClient({"kb": ["a.pdf"]})

    names = SourceNameIndex(c).names_for(["kb", {"id": "scratch", "source_ids": ["s1"]}])

    assert names == {"a.pdf"}
    assert c.calls == ["kb"]


def test_a_failing_lookup_does_not_take_the_answer_down():
    class Broken:
        def list_kb_sources(self, kb_id):
            raise RuntimeError("powabase is down")

    assert SourceNameIndex(Broken()).names_for(["kb"]) == frozenset()


def test_indexing_a_document_fires_the_invalidation_hook():
    """The wiring that makes the cache exact. Every document that becomes
    answerable goes through add_source_to_kb, so the hook lives there rather
    than at the call sites — but a hook nothing calls is just a comment."""
    from app.clients.powabase_client import PowabaseClient

    class Resp:
        status_code = 200
        def json(self): return {}

    c = PowabaseClient("https://example", "key")
    c._client = SimpleNamespace(post=lambda *a, **k: Resp())
    seen = []
    c.on_kb_write = seen.append

    c.add_source_to_kb("kb-7", "src-1")

    assert seen == ["kb-7"]


def test_the_index_subscribed_to_the_client_sees_a_new_document():
    """End to end through the hook: index, cache, add, and the next lookup
    must already include the new name without any TTL having expired."""
    from app.clients.powabase_client import PowabaseClient

    class Resp:
        status_code = 200
        def json(self): return {}

    names = ["old.pdf"]
    c = PowabaseClient("https://example", "key")
    c._client = SimpleNamespace(post=lambda *a, **k: Resp())
    c.list_kb_sources = lambda kb_id: {"items": [{"source_name": n} for n in names]}

    idx = SourceNameIndex(c, ttl=10_000.0)
    c.on_kb_write = idx.invalidate
    assert idx.names_for(["kb"]) == {"old.pdf"}

    names.append("just-added.pdf")
    c.add_source_to_kb("kb", "src-1")

    assert "just-added.pdf" in idx.names_for(["kb"])


# --- which knowledge bases the names come from -----------------------------

from app.services.source_names import knowledge_kb_ids


def test_a_specialists_own_documents_are_covered_too():
    """Both tiers can supply the context a model then talks about. Covering
    only the chatbot's knowledge would leave every document trained onto a
    specialist nameable in prose."""
    ids = knowledge_kb_ids(
        ["kb-chatbot"],
        [{"id": "ag1", "kb_id": "kb-agent", "kb_full_id": "kb-agent-full"}],
    )

    assert set(ids) == {"kb-chatbot", "kb-agent", "kb-agent-full"}


def test_agents_without_knowledge_contribute_nothing():
    ids = knowledge_kb_ids(["kb-chatbot"], [{"id": "ag1", "kb_id": None,
                                             "kb_full_id": None}])

    assert ids == ["kb-chatbot"]


def test_a_knowledge_base_shared_by_two_agents_is_listed_once():
    ids = knowledge_kb_ids([], [{"kb_id": "kb-x"}, {"kb_id": "kb-x"}])

    assert ids == ["kb-x"]
