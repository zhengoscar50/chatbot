from __future__ import annotations

from app.clients.powabase_client import PowabaseAPIError

STAGES = ["Researching", "Analyzing", "Writing"]


def build_message(query: str, evidence: str) -> str:
    return (
        "CONTEXT:\n" + (evidence or "(no relevant excerpts found)") +
        "\n\nRESEARCH QUESTION:\n" + query
    )


def run_research(client, orchestration_id: str, job: dict, message: str) -> None:
    """Background worker: stream the orchestration into the job."""
    steps = {"n": 0}

    def on_event(name, data):
        if name == "sequential_step":
            steps["n"] += 1
            idx = min(steps["n"], len(STAGES) - 1)
            job["stage"] = STAGES[idx]
        elif name == "complete":
            if data.get("status") == "failed" or data.get("error"):
                job["status"] = "failed"; job["detail"] = data.get("error") or "Research run failed"
            else:
                job["report"] = data.get("content") or ""
                job["status"] = "done"
        elif name == "error":
            job["status"] = "failed"; job["detail"] = data.get("error") or data.get("message") or "Research run failed"

    try:
        client.run_orchestration_stream(orchestration_id, message, on_event)
    except PowabaseAPIError as e:
        job["status"] = "failed"; job["detail"] = str(e)
    except Exception as e:  # noqa: BLE001 — background worker must never leave a job stuck at "running"
        job["status"] = "failed"; job["detail"] = str(e) or e.__class__.__name__
    if job["status"] == "running":  # stream ended without a terminal event
        job["status"] = "failed" if job["report"] is None else "done"
