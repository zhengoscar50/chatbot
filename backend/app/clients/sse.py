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
        events.append({"event": event_name or "message", "data": data})

    for line in text.splitlines():
        if line == "":
            flush()
            event_name = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())

    flush()
    return events
