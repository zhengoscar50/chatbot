"""Verify chatbot knowledge after migration 012.

The failure this must catch is two chatbots sharing one knowledge base — the
exact leak the migration's oldest-chatbot subselect exists to prevent.

    python -m scripts.verify_chatbot_kb
"""
import os
import sys

import httpx

# Measured against the live project on 2026-08-19, BEFORE migration 012 was
# applied: (had_chunked, had_full) per user.
#
# Only oscarzheng has personal knowledge, and only in the whole-document tier —
# every document trained there was short enough to index whole, so the chunked
# tier was never lazily created. After 012 exactly one chatbot should carry
# that kb_full_id and no chatbot should carry any kb_id.
EXPECTED = {
    "oscar":                (False, False),
    "oscarzheng":           (False, True),
    "smoketest_1787085226": (False, False),
    "zheng":                (False, False),
}

BASE = os.environ["POWABASE_BASE_URL"].rstrip("/")
KEY = os.environ["POWABASE_SERVICE_ROLE_KEY"]
H = {"apikey": KEY, "Authorization": "Bearer " + KEY}


def rows(path, **params):
    r = httpx.get(BASE + path, params=params, headers=H, timeout=30.0)
    r.raise_for_status()
    return r.json()


users = {u["id"]: u for u in rows("/rest/v1/users", select="id,username,kb_id,kb_full_id")}
bots = rows("/rest/v1/chatbots", select="id,owner_id,name,kb_id,kb_full_id,created_at")

ok = True

# Check 1: no two chatbots share a knowledge base. THE critical check.
seen = {}
for b in bots:
    for kb in (b.get("kb_id"), b.get("kb_full_id")):
        if not kb:
            continue
        if kb in seen:
            print("SHARED KB %s: %s and %s" % (kb, seen[kb], b["id"]))
            ok = False
        seen[kb] = b["id"]
print("chatbots sharing a knowledge base :", 0 if ok else "SEE ABOVE")

# Check 2: every user who had knowledge has exactly one chatbot carrying it.
for uid, u in users.items():
    mine = [b for b in bots if b["owner_id"] == uid]
    for column in ("kb_id", "kb_full_id"):
        carriers = [b for b in mine if b.get(column) == u.get(column) and u.get(column)]
        want = 1 if u.get(column) else 0
        if len(carriers) != want:
            print("%s %s: %d chatbots carry it, want %d"
                  % (u["username"], column, len(carriers), want))
            ok = False

# Check 3: the baseline recorded before the migration still describes reality.
for username, (had_chunked, had_full) in EXPECTED.items():
    u = next((x for x in users.values() if x["username"] == username), None)
    if u is None:
        print("user %s vanished" % username)
        ok = False
        continue
    if bool(u.get("kb_id")) != had_chunked or bool(u.get("kb_full_id")) != had_full:
        print("%s: personal pointers changed, migration should not touch users"
              % username)
        ok = False

print("VERDICT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
