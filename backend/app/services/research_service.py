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
    # Powabase's sequential_step fires at each step's START with a 0-based
    # "step" field (0=researcher, 1=analyst, 2=writer), so index STAGES by it
    # directly — the label tracks the agent actually working. Fall back to a
    # counter only if a future event ever omits "step".
    steps = {"n": -1}

    def on_event(name, data):
        if name == "sequential_step":
            step = data.get("step")
            if step is None:
                steps["n"] += 1
                step = steps["n"]
            job["stage"] = STAGES[min(step, len(STAGES) - 1)]
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
