"""Prove the getting-started checklist reports a REAL account correctly.

    cd backend && set -a && . .env && set +a && \
      .venv/bin/python ../tools/verify_onboarding.py

Every unit test for this feature runs against fakes. This signs up a throwaway
account against a running backend and checks the checklist it gets back: that
a brand-new account starts with only the starter-chatbot step ticked, that the
five steps always arrive in the documented order with server-owned copy, and
that the endpoint refuses an unauthenticated caller.

Requires the backend running on localhost:8000.
"""
import os
import sys
import time

import httpx

BASE = "http://localhost:8000"
NAME = f"onboard_{int(time.time())}"
PASSWORD = "verify-onboarding-pw"

failures = []


def check(ok, label, detail=""):
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}{'  — ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def steps(token):
    r = httpx.get(f"{BASE}/onboarding",
                  headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    body = r.json()
    return {s["id"]: s["done"] for s in body["steps"]}, body["complete"]


# Registration may be invite-gated on a shared deployment (SIGNUP_INVITE_CODE
# in the environment). Ask the server rather than assuming either way.
policy = httpx.get(f"{BASE}/auth/signup-policy", timeout=30)
policy.raise_for_status()
invite_required = policy.json()["invite_required"]
print(f"signup-policy: invite_required={invite_required}")

signup_body = {"username": NAME, "password": PASSWORD}
if invite_required:
    code = os.environ.get("SIGNUP_INVITE_CODE")
    if not code:
        print("FAIL — signup is invite-gated and SIGNUP_INVITE_CODE is not "
              "set in this environment; cannot register a throwaway account")
        sys.exit(1)
    signup_body["invite_code"] = code

r = httpx.post(f"{BASE}/auth/register", json=signup_body, timeout=60)
r.raise_for_status()
token = r.json()["token"]
print(f"signed up {NAME}")

print("\n=== a brand new account ===")
done, complete = steps(token)
check(done["chatbot"] is True, "the starter chatbot ticks step 1")
check(done["agent"] is False, "no agent yet")
check(done["description"] is False, "no description yet")
check(done["knowledge"] is False, "no knowledge yet")
check(done["answer"] is False, "no specialist answer yet")
check(complete is False, "a fresh account is not complete")

print("\n=== payload shape ===")
r = httpx.get(f"{BASE}/onboarding",
              headers={"Authorization": f"Bearer {token}"}, timeout=30)
body = r.json()
check(len(body["steps"]) == 5, "five steps", str(len(body["steps"])))
check([s["id"] for s in body["steps"]] ==
      ["chatbot", "agent", "description", "knowledge", "answer"],
      "steps arrive in the documented order")
check(all(s["label"].strip() and s["hint"].strip() for s in body["steps"]),
      "every step carries server-owned copy")

print("\n=== authentication ===")
r = httpx.get(f"{BASE}/onboarding", timeout=30)
check(r.status_code == 401, "unauthenticated access is refused", str(r.status_code))

print("\n=== the answer step's discriminator, against live data ===")
# The step that proves the app worked ticks on answered_by_id alone. Every
# other test of it runs against fakes, which agree with whatever we believed.
# This asks the real service.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.clients.powabase_client import PowabaseClient  # noqa: E402

pb_base = os.environ.get("POWABASE_BASE_URL", "").rstrip("/")
pb_key = os.environ.get("POWABASE_SERVICE_ROLE_KEY", "")
if not pb_base or not pb_key:
    print("  [skip] POWABASE_BASE_URL / POWABASE_SERVICE_ROLE_KEY not set")
else:
    pb = httpx.Client(base_url=pb_base, timeout=30,
                      headers={"apikey": pb_key, "Authorization": f"Bearer {pb_key}"})
    client = PowabaseClient.__new__(PowabaseClient)
    client._client = pb

    check(client.has_specialist_answer([]) is False,
          "no sessions returns False without a query")

    rows = pb.get("/rest/v1/messages",
                  params={"select": "session_id", "answered_by_id": "not.is.null",
                          "limit": 1}).json()
    if rows:
        sid = rows[0]["session_id"]
        check(client.has_specialist_answer([sid]) is True,
              "a session a specialist answered returns True", sid)
    else:
        print("  [skip] no specialist-answered message exists in this project yet")

    # The negative polarity is the one that matters: a chat the general
    # assistant handled must NOT tick the step.
    allm = pb.get("/rest/v1/messages",
                  params={"select": "session_id,answered_by_id"}).json()
    by_session = {}
    for m in allm:
        by_session.setdefault(m["session_id"], []).append(m["answered_by_id"])
    general_only = [s for s, ids in by_session.items() if not any(ids)]
    if general_only:
        check(client.has_specialist_answer([general_only[0]]) is False,
              "a chat only the general assistant answered returns False",
              general_only[0])
    else:
        print("  [skip] no general-assistant-only session exists in this project")
    pb.close()


print("\n" + "=" * 60)
if failures:
    print(f"FAILED — {len(failures)} check(s):")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASS — the checklist reports a real account correctly")
