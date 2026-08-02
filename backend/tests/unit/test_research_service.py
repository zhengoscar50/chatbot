from app.services.research_service import build_message, run_research, STAGES


def test_build_message_has_context_and_question():
    m = build_message("What changed?", "Excerpt A [1]\nExcerpt B [2]")
    assert "CONTEXT:" in m and "Excerpt A [1]" in m and "RESEARCH QUESTION:" in m and "What changed?" in m


class FakeStreamClient:
    def __init__(self, events):
        self.events = events

    def run_orchestration_stream(self, oid, message, on_event):
        for name, data in self.events:
            on_event(name, data)


def test_run_research_advances_stage_and_captures_report():
    events = [
        ("sequential_step", {}),
        ("sequential_step", {}),
        ("sequential_step", {}),
        ("complete", {"content": "Final report."}),
    ]
    job = {
        "status": "running",
        "stage": STAGES[0],
        "report": None,
        "citations": [],
        "detail": None,
        "owner": "o1",
    }
    run_research(FakeStreamClient(events), "orch-1", job, "msg")
    assert job["status"] == "done" and job["report"] == "Final report."


def test_run_research_marks_failed_on_error_event():
    job = {
        "status": "running",
        "stage": STAGES[0],
        "report": None,
        "citations": [],
        "detail": None,
        "owner": "o1",
    }
    run_research(FakeStreamClient([("error", {"error": "boom"})]), "orch-1", job, "msg")
    assert job["status"] == "failed" and "boom" in (job["detail"] or "")
