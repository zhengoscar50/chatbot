"""Prove the reasoning-effort control is not inert. Run against a live Powabase.

    cd backend && set -a && . .env && set +a && \
      .venv/bin/python ../tools/verify_reasoning_effort.py

Powabase passes an agent's `settings` to the provider WITHOUT validating it, so
a key a provider ignores is accepted, stored, and returned as if it worked. No
unit test can catch that — the only evidence is the provider's own token
accounting.

This creates one throwaway agent per level, asks each the same question, and
asserts the reasoning-token counts genuinely differ. Run it whenever a model is
added to SUPPORTS_EFFORT in app/services/reasoning.py; if the numbers do not
move, that model does not belong on the list.

Every agent created here is deleted, including on failure.
"""
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.services.reasoning import SUPPORTS_EFFORT, effort_settings  # noqa: E402

B = os.environ["POWABASE_BASE_URL"].rstrip("/")
K = os.environ["POWABASE_SERVICE_ROLE_KEY"]
H = {"apikey": K, "Authorization": "Bearer " + K}

# Calibration matters. Too easy and every level reports zero reasoning tokens,
# proving nothing; too hard and high exceeds the gateway's timeout before it
# answers. This sits in between: enough steps to reward more thinking, few
# enough to finish.
QUESTION = (
    "A bat and ball cost $1.10 together. The bat costs $1.00 more than the "
    "ball. A second ball costs twice the first. A box holds 3 bats and 5 of "
    "the second ball. What does the box cost? Answer with the number only."
)

created = []


def cleanup():
    for a in created:
        try:
            httpx.delete(B + f"/api/agents/{a}", headers=H, timeout=60)
        except Exception:
            pass


def measure(model, effort):
    settings = effort_settings(model, effort)
    body = {"name": f"verify-{int(time.time() * 1000) % 1000000}", "model": model,
            "system_prompt": "Think it through, then answer."}
    if settings:
        body["settings"] = settings
    r = httpx.post(B + "/api/agents", headers=H, json=body, timeout=90)
    r.raise_for_status()
    agent_id = r.json()["id"]
    created.append(agent_id)

    t0 = time.time()
    try:
        run = httpx.post(B + f"/api/agents/{agent_id}/run", headers=H,
                         json={"message": QUESTION}, timeout=300)
        run.raise_for_status()
    except Exception as e:
        # A gateway timeout is itself evidence the setting is doing something,
        # but it is not evidence of HOW MUCH — report it rather than crashing
        # the whole run and losing the other models.
        return {"sent": settings, "seconds": round(time.time() - t0, 1),
                "reasoning": None, "completion": None,
                "error": type(e).__name__}
    usage = run.json().get("usage") or {}
    return {
        "sent": settings,
        "seconds": round(time.time() - t0, 1),
        "reasoning": usage.get("reasoning_tokens"),
        "completion": usage.get("completion_tokens"),
        "error": None,
    }


failures = []
try:
    for model in sorted(SUPPORTS_EFFORT):
        print(f"\n=== {model} ===")
        seen = {}
        for level in ("low", "high"):
            m = measure(model, level)
            seen[level] = m
            if m["error"]:
                print(f"  {level:<5} {m['seconds']:>5}s  {m['error']} — inconclusive")
            else:
                print(f"  {level:<5} {m['seconds']:>5}s  reasoning={m['reasoning']:<6} "
                      f"completion={m['completion']:<6} sent={m['sent']}")

        low, high = seen["low"], seen["high"]
        if low["error"] or high["error"]:
            print(f"  -> inconclusive for {model}; not counted as a failure")
        elif low["reasoning"] is None or high["reasoning"] is None:
            failures.append(f"{model}: provider reports no reasoning_tokens at all")
        elif high["reasoning"] <= low["reasoning"]:
            failures.append(
                f"{model}: high ({high['reasoning']}) did not exceed low "
                f"({low['reasoning']}) — the setting is being ignored")
        else:
            ratio = high["reasoning"] / max(low["reasoning"], 1)
            print(f"  -> high used {high['reasoning'] - low['reasoning']} more "
                  f"reasoning tokens ({ratio:.1f}x). Setting is live.")
finally:
    cleanup()
    print(f"\ncleaned up {len(created)} throwaway agents")

print("\n" + "=" * 70)
if failures:
    print("FAILED — these models do not honour the setting and must be removed")
    print("from SUPPORTS_EFFORT in app/services/reasoning.py:")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print(f"PASS — all {len(SUPPORTS_EFFORT)} listed models measurably honour the setting")
