from fastapi import Request

RESEARCHER_NAME = "research-researcher"
ANALYST_NAME = "research-analyst"
WRITER_NAME = "research-writer"
ORCHESTRATION_NAME = "deep-research-pipeline"

RESEARCHER_PROMPT = (
    "You are a research assistant. The user's message has a CONTEXT section "
    "(retrieved document excerpts, each with a [n] citation marker) and a "
    "RESEARCH QUESTION. Extract the key facts, claims, and figures from the "
    "CONTEXT that bear on the question, as a tight bulleted list, keeping each "
    "point's [n] citation marker. Do not add outside knowledge; if the context "
    "is thin, say so."
)
ANALYST_PROMPT = (
    "You are an analyst. Given the researcher's extracted facts (with [n] "
    "markers), synthesize an analysis of the research question: group themes, "
    "compare/contrast, note tensions or gaps, and draw supported conclusions. "
    "Keep the [n] markers on the claims they support. Reason carefully."
)
WRITER_PROMPT = (
    "You are a technical writer. Turn the analyst's synthesis into a clear, "
    "structured markdown report answering the research question: a short summary, "
    "then sections with headers, then a brief conclusion. Preserve the [n] "
    "citation markers inline. Do not invent facts beyond the analysis."
)


def _find_by_name(items, name):
    return next((i for i in items if i.get("name") == name), None)


def ensure_research_pipeline(
    client, researcher_model: str, analyst_model: str, writer_model: str
) -> str:
    """Find-or-create the shared researcher/analyst/writer pipeline; return its
    orchestration id. Called once at startup and reused by every research run."""
    existing_agents = client.list_agents().get("agents", [])

    def ensure_agent(name, model, prompt):
        found = _find_by_name(existing_agents, name)
        if found:
            return found["id"]
        created = client.create_agent(name, model=model, system_prompt=prompt)
        existing_agents.append(created)
        return created["id"]

    r = ensure_agent(RESEARCHER_NAME, researcher_model, RESEARCHER_PROMPT)
    a = ensure_agent(ANALYST_NAME, analyst_model, ANALYST_PROMPT)
    w = ensure_agent(WRITER_NAME, writer_model, WRITER_PROMPT)

    orchestrations = client.list_orchestrations().get("orchestrations", [])
    orch = _find_by_name(orchestrations, ORCHESTRATION_NAME)
    if orch is not None:
        return orch["id"]
    orch = client.create_orchestration(ORCHESTRATION_NAME, "sequential")
    for position, agent_id in enumerate((r, a, w)):
        role = ("researcher", "analyst", "writer")[position]
        client.add_orchestration_entity(orch["id"], agent_id, role, position)
    return orch["id"]


def get_research_orchestration_id(request: Request) -> str:
    """FastAPI dependency returning the orchestration id resolved at startup."""
    return request.app.state.research_orchestration_id
