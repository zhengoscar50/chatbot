"""Verify the chatbot backfill against the pre-migration baseline.

Compares live row counts against the recorded pre-migration baseline and proves
no row was dropped or left unstamped during the backfill.

    python -m scripts.verify_chatbot_backfill
"""
import os
import sys

import httpx

# Counts recorded before the migration. The backfill only stamps a column,
# so it must not change them.
EXPECTED = {
    "oscarzheng": {"agents": 11, "chats": 18},
    "oscar":      {"agents": 5,  "chats": 4},
    "zheng":      {"agents": 0,  "chats": 3},
}

BASE = os.environ["POWABASE_BASE_URL"].rstrip("/")
KEY = os.environ["POWABASE_SERVICE_ROLE_KEY"]
H = {"apikey": KEY, "Authorization": "Bearer " + KEY}


def rows(path, **params):
    r = httpx.get(BASE + path, params=params, headers=H, timeout=30.0)
    r.raise_for_status()
    return r.json()


users = {u["id"]: u["username"] for u in rows("/rest/v1/users", select="id,username")}
agents = rows("/rest/v1/agents", select="owner_id,chatbot_id")
sessions = rows("/rest/v1/sessions", select="owner_id,chatbot_id")
bots = rows("/rest/v1/chatbots", select="id,owner_id,name")

ok = True

# Check 1: no agent or session has null/missing chatbot_id
orphan_agents = [a for a in agents if not a.get("chatbot_id")]
orphan_chats = [s for s in sessions if not s.get("chatbot_id")]
print("agents without a chatbot :", len(orphan_agents))
ok &= not orphan_agents
print("chats without a chatbot  :", len(orphan_chats))
ok &= not orphan_chats

# Check 2: each owner with content has exactly 1 chatbot (holds at backfill time only; users create more later)
owners_with_content = {a["owner_id"] for a in agents} | {s["owner_id"] for s in sessions}
per_owner = {}
for b in bots:
    per_owner.setdefault(b["owner_id"], []).append(b)
for owner in owners_with_content:
    n = len(per_owner.get(owner, []))
    username = users.get(owner, owner)
    print("%-14s chatbots: %d" % (username[:14], n))
    ok &= n == 1

# Check 3: per-user counts match the pre-migration baseline
print()
username_to_id = {v: k for k, v in users.items()}
for username, expected_counts in EXPECTED.items():
    if username not in username_to_id:
        print("missing from database: %s" % username)
        ok = False
        continue
    owner_id = username_to_id[username]
    agent_count = sum(1 for a in agents if a["owner_id"] == owner_id)
    chat_count = sum(1 for s in sessions if s["owner_id"] == owner_id)
    print("%-14s %2d agents (expected %2d), %2d chats (expected %2d)" % (
        username[:14], agent_count, expected_counts["agents"],
        chat_count, expected_counts["chats"]))
    ok &= agent_count == expected_counts["agents"]
    ok &= chat_count == expected_counts["chats"]

print("\nVERDICT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
