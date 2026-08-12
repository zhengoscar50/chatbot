"""Does retrieval still work once a conversation has history?

29a62fc fixed "conversation history was polluting the retrieval query": the
app searched with the whole transcript blob, which drowned the question and
degraded retrieval as the chat grew. The fix searched the question instead,
via a context handler that takes its own `query`.

Moving to runtime_knowledge_bases gives that property up: the agent gets a
knowledge_search tool and writes its own search terms from a message that
still carries the inlined history. That should be *better* than searching the
blob — a model reading the conversation can tell which part is the question —
but "should be" is not evidence.

This builds a deliberately noisy conversation and then asks a question whose
answer is one line in one document. If retrieval survives that, the delegation
holds.

    SMOKE_BASE_URL=http://127.0.0.1:8001 python -m scripts.check_history_retrieval
"""
import io
import os
import re
import secrets
import sys
import time

import httpx

C = httpx.Client(
    base_url=os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000"), timeout=240.0
)

# Chatter first, so the transcript is mostly noise by the time it matters.
NOISE = [
    "hey, how are you today?",
    "what can you help me with?",
    "thanks. I'm preparing for a site visit next week.",
    "the weather has been miserable lately, hasn't it",
    "anyway, remind me what you can do",
]
FACT = b"Site handbook: the emergency assembly point is BEHIND THE NORTH ANNEXE.\n"
QUESTION = "Where is the emergency assembly point?"
ACCEPTED = ("behind the north annexe", "north annexe")

USERNAME = "histcheck-" + str(int(time.time()))
H = {"Authorization": "Bearer " + C.post(
    "/auth/register",
    json={"username": USERNAME, "password": secrets.token_urlsafe(24)},
).json()["token"]}


def flat(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower())


agent = C.post("/agents", headers=H, json={
    "name": "Site guide",
    "description": "Site handbook: access, safety procedures and assembly points.",
    "grounding": "strict"}).json()["id"]
C.post("/agents/%s/train" % agent, headers=H,
       files={"file": ("handbook.txt", io.BytesIO(FACT), "text/plain")})

chat = C.post("/sessions", headers=H, json={}).json()["id"]

print("building %d turns of noise before the real question\n" % len(NOISE))
for turn in NOISE:
    C.post("/chat", headers=H, json={"session_id": chat, "query": turn})

body = C.post("/chat", headers=H,
              json={"session_id": chat, "query": QUESTION}).json()
answer = body.get("answer") or body.get("detail") or ""
citations = body.get("citations") or []

transcript = C.get("/sessions/%s/messages" % chat, headers=H).json()["messages"]
print("transcript length at question time: %d messages" % len(transcript))
print("answer:", answer[:160])
print("citations:", len(citations))

grounded = any(flat(a) in flat(answer) for a in ACCEPTED)
print("\nretrieved the fact through the noise:", grounded)
print("cited a source:", bool(citations))

# Clean up after ourselves; this runs against the shared project.
C.delete("/agents/" + agent, headers=H)
admin_pw = os.environ.get("ADMIN_PASSWORD")
if admin_pw:
    admin = {"X-Admin-Password": admin_pw}
    for u in C.get("/admin/users", headers=admin).json():
        if u.get("username") == USERNAME:
            C.delete("/admin/users/" + u["id"], headers=admin)
            print("cleanup: removed", USERNAME)

sys.exit(0 if grounded and citations else 1)
