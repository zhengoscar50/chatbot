from datetime import date

from app.services.share_service import ShareService, redact_citations, redact_turn


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


def test_the_same_document_keeps_one_label_when_the_id_comes_first():
    out = redact_citations([
        {"key": 1, "source_id": "u-1", "source_name": "a.pdf", "text_excerpt": "one"},
        {"key": 2, "source_name": "a.pdf", "text_excerpt": "two"},
    ])
    assert [c["source_name"] for c in out] == ["Source 1", "Source 1"]


def test_the_same_document_keeps_one_label_when_the_id_comes_second():
    out = redact_citations([
        {"key": 1, "source_name": "a.pdf", "text_excerpt": "one"},
        {"key": 2, "source_id": "u-1", "source_name": "a.pdf", "text_excerpt": "two"},
    ])
    assert [c["source_name"] for c in out] == ["Source 1", "Source 1"]


def test_two_documents_sharing_a_filename_stay_separate():
    """source_id is the real identity — two documents can legitimately share a
    filename, and merging them would attribute one's text to the other."""
    out = redact_citations([
        {"key": 1, "source_id": "u-1", "source_name": "report.pdf", "text_excerpt": "one"},
        {"key": 2, "source_id": "u-2", "source_name": "report.pdf", "text_excerpt": "two"},
    ])
    assert [c["source_name"] for c in out] == ["Source 1", "Source 2"]


def test_redact_turn_replaces_the_filename_in_the_answer_with_its_label():
    answer, citations = redact_turn(
        "That's from Q3_confidential.pdf, page 2.",
        [{"key": 1, "source_id": "u-1", "source_name": "Q3_confidential.pdf",
          "text_excerpt": "revenue rose"}],
    )
    assert "Q3_confidential.pdf" not in answer
    assert citations[0]["source_name"] == "Source 1"
    assert "Source 1" in answer


def test_redact_turn_leaves_an_answer_untouched_when_the_filename_is_absent():
    answer, citations = redact_turn(
        "The revenue rose year over year.",
        [{"key": 1, "source_id": "u-1", "source_name": "Q3_confidential.pdf",
          "text_excerpt": "revenue rose"}],
    )
    assert answer == "The revenue rose year over year."
    assert citations[0]["source_name"] == "Source 1"


def test_redact_turn_handles_overlapping_filenames_longest_first():
    answer, citations = redact_turn(
        "See annual_report.pdf, which cites report.pdf directly.",
        [{"key": 1, "source_id": "u-1", "source_name": "annual_report.pdf",
          "text_excerpt": "one"},
         {"key": 2, "source_id": "u-2", "source_name": "report.pdf",
          "text_excerpt": "two"}],
    )
    assert "annual_report.pdf" not in answer
    assert "report.pdf" not in answer
    by_key = {c["key"]: c["source_name"] for c in citations}
    assert by_key[1] == "Source 1"
    assert by_key[2] == "Source 2"
    assert "Source 1" in answer
    assert "Source 2" in answer


def test_redact_turn_with_no_citations_returns_the_answer_unchanged():
    answer, citations = redact_turn("Just an answer with no sources.", [])
    assert answer == "Just an answer with no sources."
    assert citations == []


class FakeClient:
    def __init__(self, rows=None):
        self.rows = {r["id"]: r for r in (rows or [])}
        self.updates = []

    def update_chatbot_row(self, chatbot_id, fields):
        self.updates.append((chatbot_id, fields))
        self.rows.setdefault(chatbot_id, {"id": chatbot_id}).update(fields)

    def get_chatbot_by_share_token(self, token):
        return next((r for r in self.rows.values() if r.get("share_token") == token), None)


def bot(**over):
    return dict({"id": "cb-1", "share_token": None, "share_daily_limit": 3,
                 "share_used_today": 0, "share_used_date": None}, **over)


def test_enable_returns_a_long_unguessable_token():
    client = FakeClient([bot()])
    token = ShareService(client).enable("cb-1")
    assert len(token) >= 32
    assert client.rows["cb-1"]["share_token"] == token


def test_enabling_again_replaces_the_old_token():
    """Regeneration IS revocation-and-reissue: the old link must die."""
    client = FakeClient([bot()])
    service = ShareService(client)
    first = service.enable("cb-1")
    second = service.enable("cb-1")
    assert first != second
    assert service.resolve(first) is None
    assert service.resolve(second)["id"] == "cb-1"


def test_disable_removes_the_token():
    client = FakeClient([bot(share_token="tok")])
    ShareService(client).disable("cb-1")
    assert client.rows["cb-1"]["share_token"] is None


def test_resolving_an_unknown_or_empty_token_is_none():
    client = FakeClient([bot(share_token="tok")])
    service = ShareService(client)
    assert service.resolve("nope") is None
    assert service.resolve("") is None
    assert service.resolve(None) is None


def test_consume_allows_up_to_the_limit_then_refuses():
    client = FakeClient([bot(share_daily_limit=2)])
    service = ShareService(client)
    row = client.rows["cb-1"]
    assert service.consume(row, today=date(2026, 1, 1)) is True
    assert service.consume(client.rows["cb-1"], today=date(2026, 1, 1)) is True
    writes_before_refusal = len(client.updates)
    assert service.consume(client.rows["cb-1"], today=date(2026, 1, 1)) is False
    assert len(client.updates) == writes_before_refusal


def test_a_new_day_resets_the_counter():
    """The whole point of storing the date beside the count: no scheduled job
    resets anything, a request on a new date does it."""
    client = FakeClient([bot(share_daily_limit=1, share_used_today=1,
                             share_used_date="2026-01-01")])
    service = ShareService(client)
    assert service.consume(client.rows["cb-1"], today=date(2026, 1, 1)) is False
    assert service.consume(client.rows["cb-1"], today=date(2026, 1, 2)) is True


def test_a_zero_limit_refuses_everything():
    client = FakeClient([bot(share_daily_limit=0)])
    assert client.updates == []
    assert ShareService(client).consume(client.rows["cb-1"], today=date(2026, 1, 1)) is False
    assert client.updates == []


def test_has_room_is_true_under_the_limit():
    client = FakeClient([bot(share_daily_limit=2, share_used_today=1,
                             share_used_date="2026-01-01")])
    service = ShareService(client)
    assert service.has_room(client.rows["cb-1"], today=date(2026, 1, 1)) is True


def test_has_room_is_false_at_the_limit():
    client = FakeClient([bot(share_daily_limit=2, share_used_today=2,
                             share_used_date="2026-01-01")])
    service = ShareService(client)
    assert service.has_room(client.rows["cb-1"], today=date(2026, 1, 1)) is False


def test_has_room_is_true_again_on_a_new_date():
    client = FakeClient([bot(share_daily_limit=1, share_used_today=1,
                             share_used_date="2026-01-01")])
    service = ShareService(client)
    assert service.has_room(client.rows["cb-1"], today=date(2026, 1, 1)) is False
    assert service.has_room(client.rows["cb-1"], today=date(2026, 1, 2)) is True


def test_has_room_writes_nothing():
    client = FakeClient([bot(share_daily_limit=2, share_used_today=2,
                             share_used_date="2026-01-01")])
    service = ShareService(client)
    service.has_room(client.rows["cb-1"], today=date(2026, 1, 1))
    service.has_room(client.rows["cb-1"], today=date(2026, 1, 2))
    assert client.updates == []
