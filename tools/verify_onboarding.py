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

print("\n" + "=" * 60)
if failures:
    print(f"FAILED — {len(failures)} check(s):")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASS — the checklist reports a real account correctly")
