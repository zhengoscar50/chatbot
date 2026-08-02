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


class StageRecordingClient:
    """Records job['stage'] after each streamed event so the test can assert
    the full stage progression, not just the terminal state."""

    def __init__(self, events, job):
        self.events = events
        self.job = job
        self.stages = []

    def run_orchestration_stream(self, oid, message, on_event):
        for name, data in self.events:
            on_event(name, data)
            self.stages.append(self.job["stage"])


def test_run_research_advances_stage_by_step_field_and_captures_report():
    # Real Powabase sequential_step carries a 0-based "step" (0=researcher …).
    events = [
        ("sequential_step", {"step": 0, "agent": "research-researcher"}),
        ("sequential_step", {"step": 1, "agent": "research-analyst"}),
        ("sequential_step", {"step": 2, "agent": "research-writer"}),
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
    client = StageRecordingClient(events, job)
    run_research(client, "orch-1", job, "msg")
    # Label tracks the agent actually working: Researching -> Analyzing -> Writing.
    assert client.stages[:3] == ["Researching", "Analyzing", "Writing"]
    assert job["status"] == "done" and job["report"] == "Final report."


def test_run_research_falls_back_to_counter_when_step_absent():
    events = [
        ("sequential_step", {}),
        ("sequential_step", {}),
        ("sequential_step", {}),
        ("complete", {"content": "Report."}),
    ]
    job = {
        "status": "running",
        "stage": STAGES[0],
        "report": None,
        "citations": [],
        "detail": None,
        "owner": "o1",
    }
    client = StageRecordingClient(events, job)
    run_research(client, "orch-1", job, "msg")
    assert client.stages[:3] == ["Researching", "Analyzing", "Writing"]
    assert job["status"] == "done"


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


class RaisingStreamClient:
    def run_orchestration_stream(self, oid, message, on_event):
        raise RuntimeError("connection reset")


def test_run_research_marks_failed_on_unexpected_exception():
    job = {
        "status": "running",
        "stage": STAGES[0],
        "report": None,
        "citations": [],
        "detail": None,
        "owner": "o1",
    }
    run_research(RaisingStreamClient(), "orch-1", job, "msg")
    assert job["status"] == "failed" and "connection reset" in (job["detail"] or "")
