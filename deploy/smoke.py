#!/usr/bin/env python3
"""Exercise the authenticated routes against the real Powabase, over real HTTP.

Every other check in this repo stops short of this. Unit tests answer "is the
logic right" against fakes written by the same person as the query — which is
how a SELECT for a column that does not exist passed 649 tests, nine mutation
checks and the preflight gate, then failed on the owner's first click.
preflight proves routes are mounted and answering, but a 401 from an auth-gated
route looks identical whether the query behind it works or not.

So this mints a real token, calls the real routes over HTTP, and asserts on
what comes back.

**It cleans up after itself and touches nothing else.** The visitor
conversation it inspects is one it creates through the public share route, and
it deletes only that. If a check fails partway, cleanup still runs.

    cd ~/rag-chatbot/backend && set -a && . ./.env && set +a
    .venv/bin/python ../deploy/smoke.py --token <share-token>
"""
import argparse
import os
import sys
import uuid

# Importable whatever the cwd is: the app package lives in ../backend relative
# to this file, and Python puts the SCRIPT's directory on the path, not the
# working directory — so running it from anywhere else finds nothing.
_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, os.path.abspath(_BACKEND))

import httpx

from app.core.config import get_settings
from app.core.security import create_access_token
from app.clients.powabase_client import PowabaseClient

results = []


def check(ok, label, detail=""):
    results.append((ok, label))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--token", required=True, help="a chatbot's share token")
    args = ap.parse_args()

    s = get_settings()
    client = PowabaseClient(s.powabase_base_url, s.powabase_service_role_key)

    chatbot = client.get_chatbot_by_share_token(args.token)
    if chatbot is None:
        print("No chatbot has that share token.")
        return 2
    cb_id, owner_id = chatbot["id"], chatbot["owner_id"]

    # A real token for the real owner, signed with the app's own secret. The
    # routes below cannot tell it from one the login page issued.
    auth = {"Authorization": "Bearer "
            + create_access_token(owner_id, s.auth_jwt_secret, 1)}
    http = httpx.Client(base_url=args.base, timeout=90.0)
    created = None

    try:
        print("\n=== authentication ===")
        r = http.get("/chatbots", headers=auth)
        check(r.status_code == 200, "an authenticated request is accepted",
              f"HTTP {r.status_code}")
        check(r.status_code == 200 and any(c["id"] == cb_id for c in r.json()),
              "and returns the caller's own chatbots")
        check(http.get("/chatbots").status_code == 401,
              "an unauthenticated one is refused")

        print("\n=== a visitor conversation, created for this run ===")
        r = http.post(f"/s/{args.token}/session")
        check(r.status_code == 200, "the public route creates a session",
              f"HTTP {r.status_code}")
        created = r.json().get("session_id") if r.status_code == 200 else None
        if not created:
            return 1
        marker = f"smoke test {uuid.uuid4().hex[:8]}"
        r = http.post(f"/s/{args.token}/chat",
                      json={"session_id": created, "query": marker})
        answer = r.json().get("answer") if r.status_code == 200 else None
        check(bool(answer), "and answers a message through it",
              (answer or f"HTTP {r.status_code} {r.text[:60]}")[:60])
        if r.status_code == 200:
            leaked = [c.get("source_name") for c in (r.json().get("citations") or [])
                      if str(c.get("source_name", "")).lower()
                      .endswith((".pdf", ".docx", ".txt", ".md"))]
            check(not leaked, "with no filename in the citations a stranger sees",
                  str(leaked)[:80])

        print("\n=== the inbox reads it back ===")
        r = http.get(f"/chatbots/{cb_id}/inbox", headers=auth)
        # THE check. This is the request that returned 502 in production while
        # every offline test passed: the query behind it named a column that
        # does not exist, and only a real database could say so.
        if not check(r.status_code == 200, "the inbox route answers against real data",
                     f"HTTP {r.status_code} {r.text[:120]}"):
            return 1
        rows = r.json()
        mine = next((x for x in rows if x["id"] == created), None)
        check(mine is not None, "the new conversation appears in it",
              f"{len(rows)} rows")
        if mine:
            check(mine["preview"].startswith("smoke test"),
                  "with the visitor's own question as its preview",
                  mine["preview"][:40])
            check(mine["message_count"] >= 2, "and a message count",
                  str(mine["message_count"]))

        r = http.get(f"/sessions/{created}/messages", headers=auth)
        check(r.status_code == 200, "its transcript is readable by the owner",
              f"HTTP {r.status_code}")
        check(r.status_code == 200
              and any(m.get("text") == marker
                      for m in (r.json().get("messages") or [])),
              "and contains the message that was sent")

        print("\n=== the delete guards refuse what they should ===")
        check(http.delete(f"/chatbots/{cb_id}/inbox/{uuid.uuid4()}",
                          headers=auth).status_code == 404,
              "a session that does not exist is not found")
        owner_chats = http.get(f"/sessions?chatbot_id={cb_id}", headers=auth)
        private = None
        if owner_chats.status_code == 200 and owner_chats.json():
            private = owner_chats.json()[0]["id"]
        if private:
            # The guard that matters: the owner's OWN chats live in the same
            # table, told apart only by `shared`. Refusing here is what stops
            # the inbox destroying them. Asserted on survival, not the status
            # code — a route that 404s and deletes anyway would pass that.
            code = http.delete(f"/chatbots/{cb_id}/inbox/{private}",
                               headers=auth).status_code
            still = http.get(f"/sessions/{private}/messages", headers=auth).status_code
            check(code == 404 and still == 200,
                  "the owner's own private chat is refused AND survives",
                  f"delete {code}, still readable {still}")
        else:
            check(True, "(no private chat exists to test that guard against)")

        print("\n=== cleanup removes exactly what this run made ===")
        gone = created
        code = http.delete(f"/chatbots/{cb_id}/inbox/{created}",
                           headers=auth).status_code
        if check(code == 204, "the conversation it created is deleted",
                 f"HTTP {code}"):
            created = None
        after = http.get(f"/chatbots/{cb_id}/inbox", headers=auth).json()
        # By absence, not by the list getting shorter: the inbox is capped at
        # INBOX_LIMIT, so on a chatbot with more shared sessions than that,
        # deleting one simply promotes the next into view and the count is
        # unchanged. The first version of this asserted the count and failed
        # against real data for exactly that reason.
        check(gone not in [x["id"] for x in after],
              "and it is no longer listed", f"{len(after)} rows remain")
        check(http.get(f"/sessions/{gone}/messages",
                       headers=auth).status_code == 404,
              "its transcript is gone too")
    finally:
        if created:
            http.delete(f"/chatbots/{cb_id}/inbox/{created}", headers=auth)
            print(f"\n  (cleaned up {created} after a failure)")
        http.close()

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:")
        for label in failed:
            print("  - " + label)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
