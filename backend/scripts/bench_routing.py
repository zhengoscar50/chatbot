"""Measure routing accuracy as the roster grows.

The orchestrator prompt lists every agent the user owns, so each extra agent
both lengthens the prompt and adds a way to be wrong. Every previous
measurement used TWO agents; the deployment runs seven. This script closes
that gap and can be re-run whenever the prompt, the model or the roster
changes.

    ROSTER_SIZE=7 SMOKE_BASE_URL=http://127.0.0.1:8001 python -m scripts.bench_routing

Routing is probabilistic, so every question is asked SAMPLES times in a fresh
chat and reported as a hit rate. A single sample is noise, not a measurement.
"""
import io
import os
import secrets
import sys
import time

import httpx

C = httpx.Client(
    base_url=os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000"), timeout=240.0
)
SAMPLES = int(os.environ.get("SAMPLES", "5"))
ROSTER_SIZE = int(os.environ.get("ROSTER_SIZE", "7"))
THRESHOLD = float(os.environ.get("THRESHOLD", "0.8"))  # per-question hit rate

# A deliberately realistic roster: neighbouring domains (chemistry/physics,
# contracts/tax) rather than seven unrelated topics, because near-neighbours
# are where routing actually fails. Each agent owns one unmistakable fact.
#
# The last element lists EVERY acceptable rendering of that fact. The training
# document spells numbers out; the model writes them back as numerals ("0.91"
# for "ZERO POINT NINE ONE"). A single spelling scores a correct, cited answer
# as a failure — this is the assertion trap that made the old smoke unusable,
# and the first version of this script fell into it too.
ROSTER = [
    ("Chem tutor", "Chemistry lab safety, equipment locations and chemical storage.",
     "The emergency eyewash station is in CORRIDOR SEVEN.",
     "Where is the emergency eyewash station?",
     ("corridor seven", "corridor 7")),
    ("Physics tutor", "Physics coursework: mechanics, optics and lab apparatus.",
     "The optics bench calibration constant is ZERO POINT NINE ONE.",
     "What is the optics bench calibration constant?",
     ("zero point nine one", "0 91", "0.91")),
    ("Contracts", "Vendor contracts, NDAs and termination notice periods.",
     "All vendor NDAs carry a termination notice period of NINETY DAYS.",
     "What notice period do our vendor NDAs require?",
     ("ninety days", "90 days")),
    ("Tax", "Corporate tax filings, deadlines and deductible categories.",
     "The quarterly estimated tax filing deadline is the FIFTEENTH OF APRIL.",
     "When is the quarterly estimated tax filing deadline?",
     ("fifteenth of april", "april 15", "15 april", "april fifteenth", "4 15")),
    ("HR", "Employee handbook: leave policy, benefits and onboarding.",
     "New employees accrue leave at TWO POINT FIVE DAYS per month.",
     "How fast do new employees accrue leave?",
     ("two point five days", "2 5 days", "2.5 days")),
    ("IT Support", "Internal IT: VPN, laptop provisioning and password resets.",
     "The VPN concentrator is reachable at VPN DASH EDGE DASH THREE.",
     "Which VPN concentrator should I connect to?",
     ("vpn edge three", "vpn edge 3", "vpn dash edge dash three")),
    ("Facilities", "Building access, parking, meeting rooms and maintenance.",
     "Visitor parking is on LEVEL BLUE TWO of the north garage.",
     "Where do visitors park?",
     ("level blue two", "level blue 2", "blue 2", "blue two")),
]

RESULTS = []


def ask(chat, q):
    body = C.post("/chat", headers=H, json={"session_id": chat, "query": q}).json()
    by = body.get("answered_by") or {}
    return body.get("answer") or body.get("detail") or "", by.get("name")


def new_chat():
    return C.post("/sessions", headers=H, json={}).json()["id"]


def flat(s):
    import re
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower())


def says(answer, alternatives):
    """True if the answer conveys the fact in any accepted rendering."""
    body = flat(answer)
    return any(flat(alt) in body for alt in alternatives)


# --- set up an isolated user; the roster the router sees is this user's -----
USERNAME = "bench-" + str(int(time.time()))
PASSWORD = secrets.token_urlsafe(24)
H = {"Authorization": "Bearer " + C.post(
    "/auth/register", json={"username": USERNAME, "password": PASSWORD}
).json()["token"]}

roster = ROSTER[:ROSTER_SIZE]
print("roster of %d, %d samples per question\n" % (len(roster), SAMPLES))

created = []
for name, description, fact, _, _ in roster:
    agent_id = C.post("/agents", headers=H, json={
        "name": name, "description": description, "grounding": "strict"}).json()["id"]
    created.append(agent_id)
    C.post("/agents/%s/train" % agent_id, headers=H, files={"file": (
        "%s.txt" % name.replace(" ", "_"),
        io.BytesIO(fact.encode()), "text/plain")})
print("created and trained %d agents\n" % len(created))

# --- routing ---------------------------------------------------------------
print("=== routing (need >= %.0f%% per question) ===" % (THRESHOLD * 100))
answered_right = 0
answered_total = 0
for name, _, _, question, accepted in roster:
    hits, wrong, grounded = 0, [], 0
    for _ in range(SAMPLES):
        answer, who = ask(new_chat(), question)
        if who == name:
            hits += 1
            # Routing to the right agent is worthless if the answer is wrong,
            # so check the fact too — but only on correctly routed turns.
            if says(answer, accepted):
                grounded += 1
        else:
            wrong.append(who)
    rate = hits / SAMPLES
    answered_right += grounded
    answered_total += hits
    ok = rate >= THRESHOLD
    RESULTS.append((name, ok))
    print("%-14s %-46s %d/%d  %s%s" % (
        name, question[:44], hits, SAMPLES, "PASS" if ok else "FAIL",
        "" if ok else "  -> " + str(wrong)))

# --- small talk must not be captured by a large roster ----------------------
hits = sum(1 for _ in range(SAMPLES) if ask(new_chat(), "hi there")[1] == "General assistant")
ok = hits / SAMPLES >= THRESHOLD
RESULTS.append(("small talk", ok))
print("%-14s %-46s %d/%d  %s" % ("(general)", "hi there", hits, SAMPLES,
                                 "PASS" if ok else "FAIL"))

# --- cleanup ---------------------------------------------------------------
for agent_id in created:
    C.delete("/agents/" + agent_id, headers=H)
admin_pw = os.environ.get("ADMIN_PASSWORD")
if admin_pw:
    admin = {"X-Admin-Password": admin_pw}
    for u in C.get("/admin/users", headers=admin).json():
        if u.get("username") == USERNAME:
            C.delete("/admin/users/" + u["id"], headers=admin)
            print("\ncleanup: removed %s" % USERNAME)
else:
    print("\nWARNING: ADMIN_PASSWORD unset, %s remains" % USERNAME)

passed = sum(1 for _, ok in RESULTS if ok)
print("\n%d/%d questions routed above threshold" % (passed, len(RESULTS)))
if answered_total:
    print("grounded answers on correctly routed turns: %d/%d"
          % (answered_right, answered_total))
sys.exit(0 if passed == len(RESULTS) else 1)
