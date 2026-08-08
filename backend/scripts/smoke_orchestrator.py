"""Live smoke for the agent orchestrator — a gate you can actually trust.

Two rules learned the hard way:

1. Never assert on exact model wording. "NINETY DAYS" and "90 days" are the
   same answer; a test that fails on the second is testing phrasing, not
   behaviour.
2. Routing is probabilistic — the provider is not deterministic even at
   temperature 0. A single sample is noise. Routing checks run N times and
   require a threshold, so the result means something.

Deterministic properties (attribution, isolation, persistence) are still
asserted strictly, because those genuinely must not vary.
"""
import io
import re
import sys
import time

import httpx

C = httpx.Client(base_url="http://127.0.0.1:8000", timeout=240.0)
ROUTE_SAMPLES = 5
ROUTE_THRESHOLD = 4          # >= 4 of 5

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + label + (("  :: " + str(detail)) if detail else ""))


def says(answer, *alternatives):
    """True if the answer conveys any of these, ignoring case and formatting."""
    flat = re.sub(r"[^a-z0-9]+", " ", (answer or "").lower())
    return any(re.sub(r"[^a-z0-9]+", " ", alt.lower()) in flat for alt in alternatives)


stamp = str(int(time.time()))
H = {"Authorization": "Bearer " + C.post(
    "/auth/register", json={"username": "gate-" + stamp, "password": "probe-pw-12345"}
).json()["token"]}


def new_chat():
    return C.post("/sessions", headers=H, json={}).json()["id"]


def ask(chat, q):
    body = C.post("/chat", headers=H, json={"session_id": chat, "query": q}).json()
    by = body.get("answered_by") or {}
    return body.get("answer") or body.get("detail") or "", by.get("name"), by.get("id")


def route_rate(query, expected_name, samples=ROUTE_SAMPLES):
    """Ask the same question in N fresh chats; how often does it route right?"""
    hits, seen = 0, []
    for _ in range(samples):
        _, who, _id = ask(new_chat(), query)
        seen.append(who)
        hits += (who == expected_name)
    return hits, seen


# --- setup ------------------------------------------------------------------
chem = C.post("/agents", headers=H, json={
    "name": "Chem tutor",
    "description": "Answers questions about our chemistry lab: safety procedures, "
                   "equipment locations and chemical storage.",
    "grounding": "strict"}).json()["id"]
legal = C.post("/agents", headers=H, json={
    "name": "Contracts",
    "description": "Answers questions about our vendor contracts, NDAs and payment terms.",
    "grounding": "strict"}).json()["id"]

C.post("/agents/%s/train" % chem, headers=H, files={"file": (
    "chem_handbook.txt",
    io.BytesIO(b"Lab safety: the emergency eyewash station is in CORRIDOR SEVEN.\n"
               b"Class 3 reagents are stored in VENTED CABINET B4.\n"), "text/plain")})
C.post("/agents/%s/train" % legal, headers=H, files={"file": (
    "vendor_nda.txt",
    io.BytesIO(b"All vendor NDAs carry a termination notice period of NINETY DAYS.\n"
               b"Standard payment terms are NET FORTY-FIVE from invoice date.\n"), "text/plain")})

print("=== routing (%d samples each, need >= %d) ===" % (ROUTE_SAMPLES, ROUTE_THRESHOLD))
for query, expected in (
    ("Where is the emergency eyewash station?", "Chem tutor"),
    ("Where are Class 3 reagents stored?", "Chem tutor"),
    ("What notice period do our vendor NDAs require?", "Contracts"),
    ("What are our standard payment terms?", "Contracts"),
    ("hi there", "General assistant"),
):
    hits, seen = route_rate(query, expected)
    check("%-46s -> %-17s %d/%d" % (query[:44], expected, hits, ROUTE_SAMPLES),
          hits >= ROUTE_THRESHOLD, "" if hits >= ROUTE_THRESHOLD else seen)

# --- the headline case: two agents, one conversation ------------------------
print()
print("=== two agents in ONE chat (the case threads made impossible) ===")
chat = new_chat()
a1, who1, _ = ask(chat, "Where is the emergency eyewash station?")
a2, who2, _ = ask(chat, "What notice period do our vendor NDAs require?")

check("first question answered by the chemistry agent", who1 == "Chem tutor", who1)
check("...from its document", says(a1, "corridor seven"), a1[:90])
check("second question, SAME chat, answered by the contracts agent",
      who2 == "Contracts", who2)
check("...from its document", says(a2, "ninety days", "90 days"), a2[:90])
check("no chemistry fact leaked into the contracts answer",
      not says(a2, "corridor seven"), a2[:90])
check("no contracts fact leaked into the chemistry answer",
      not says(a1, "ninety days", "90 days"), a1[:90])

# --- deterministic properties -----------------------------------------------
print()
print("=== properties that must never vary ===")
_, who, who_id = ask(chat, "hi")
check("general assistant reports a null agent id", who_id is None, who_id)

_, who_follow, _ = ask(chat, "Where is the emergency eyewash station?")
_, who_again, _ = ask(chat, "say that again")
check("a follow-up stays with the agent that just answered",
      who_again == who_follow, "%s then %s" % (who_follow, who_again))

up = C.post("/ingest/file", headers=H, data={"session_id": chat},
            files={"file": ("scratch.txt", io.BytesIO(
                b"Ephemeral note: the team offsite is on the THIRD OF MARCH in Lisbon.\n"),
                "text/plain")}).json()
# Wait on the INGEST STATUS, not by re-asking. Asking repeatedly while the
# document is still indexing writes a dozen "I don't know" turns into the
# transcript, and the model then anchors on its own earlier refusals.
indexed = False
for _ in range(20):
    time.sleep(3)
    st = C.get("/ingest/status/%s" % up["source_id"], headers=H,
               params={"session_id": chat}).json()
    if st.get("status") == "indexed":
        indexed = True
        break
check("the chat upload reaches indexed", indexed, st)
a, _, _ = ask(chat, "When is the team offsite?")
check("a chat upload is answerable once indexed", says(a, "lisbon", "third of march"), a[:90])

other = new_chat()
a_other, _, _ = ask(other, "When is the team offsite?")
check("...and is NOT visible from another chat",
      not says(a_other, "lisbon"), a_other[:90])

transcript = C.get("/sessions/%s/messages" % chat, headers=H).json()["messages"]
check("the transcript persists", len(transcript) >= 6, "%d messages" % len(transcript))
check("...and records which agent answered",
      any(m.get("answered_by") for m in transcript if m["role"] == "assistant"),
      [m.get("answered_by") for m in transcript if m["role"] == "assistant"][:4])

C.delete("/agents/" + legal, headers=H)
a_after, who_after, _ = ask(chat, "Where is the emergency eyewash station?")
check("deleting one agent leaves the chat usable", bool(a_after) and who_after is not None,
      who_after)

C.delete("/agents/" + chem, headers=H)
passed = sum(1 for _, ok in RESULTS if ok)
print("\n%d/%d passed" % (passed, len(RESULTS)))
sys.exit(0 if passed == len(RESULTS) else 1)
