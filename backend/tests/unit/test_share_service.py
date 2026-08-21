from app.services.share_service import redact_citations


def test_the_filename_never_survives():
    out = redact_citations([
        {"key": 1, "source_id": "u-1", "source_name": "Q3_confidential.pdf",
         "text_excerpt": "revenue rose"},
    ])
    assert "Q3_confidential.pdf" not in repr(out)
    assert out[0]["source_name"] == "Source 1"
    assert out[0]["text_excerpt"] == "revenue rose"


def test_the_source_id_never_survives():
    """The UI falls back to source_id when there is no name, so leaving it in
    would print a raw identifier exactly where the filename used to be."""
    out = redact_citations([
        {"key": 1, "source_id": "u-1", "source_name": "a.pdf", "text_excerpt": "x"},
    ])
    assert "source_id" not in out[0]
    assert "u-1" not in repr(out)


def test_the_same_document_keeps_one_label():
    out = redact_citations([
        {"key": 1, "source_id": "u-1", "source_name": "a.pdf", "text_excerpt": "one"},
        {"key": 2, "source_id": "u-2", "source_name": "b.pdf", "text_excerpt": "two"},
        {"key": 3, "source_id": "u-1", "source_name": "a.pdf", "text_excerpt": "three"},
    ])
    assert [c["source_name"] for c in out] == ["Source 1", "Source 2", "Source 1"]


def test_a_bare_string_citation_is_dropped():
    """The legacy citation shape IS the filename. There is nothing to redact,
    so drop it — a missing marker is better than a leaked name."""
    assert redact_citations(["secret.pdf"]) == []


def test_empty_and_none_are_safe():
    assert redact_citations([]) == []
    assert redact_citations(None) == []


def test_a_citation_with_no_excerpt_still_redacts():
    out = redact_citations([{"key": 1, "source_name": "x.pdf"}])
    assert out == [{"key": 1, "source_name": "Source 1", "text_excerpt": ""}]


def test_label_stability_when_source_id_presence_varies():
    """The same document identified by source_id in one citation and by name
    in another must receive the same label — source_id is the canonical key."""
    out = redact_citations([
        {"key": 1, "source_id": "u-1", "source_name": "a.pdf", "text_excerpt": "one"},
        {"key": 2,                     "source_name": "a.pdf", "text_excerpt": "two"},
    ])
    # Both should get "Source 1" because they're the same document (u-1 / a.pdf)
    assert [c["source_name"] for c in out] == ["Source 1", "Source 1"]


def test_unhashable_identity_is_coerced_to_string():
    """Malformed citations with unhashable source_id (e.g. dict) must degrade
    gracefully rather than crashing — coerce to string for hashability."""
    out = redact_citations([
        {"key": 1, "source_id": {"nested": "x"}, "source_name": "a.pdf", "text_excerpt": "data"},
    ])
    # Should redact gracefully, not raise TypeError
    assert len(out) == 1
    assert out[0]["source_name"] == "Source 1"
    assert "nested" not in repr(out)
