import json


def parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> None:
        if not data_lines:
            return
        payload = "\n".join(data_lines)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {"raw": payload}
        # Real Powabase agent-run streams never send a literal "event:" line —
        # the discriminator lives inside the JSON body's own "event" key. A
        # literal "event:" line still wins if a server ever sends one.
        name = event_name
        if name is None and isinstance(data, dict):
            name = data.get("event")
        events.append({"event": name or "message", "data": data})

    for line in text.splitlines():
        if line == "":
            flush()
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue  # SSE keepalive comment
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())

    flush()
    return events
