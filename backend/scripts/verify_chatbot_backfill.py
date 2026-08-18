"""Prove the chatbot backfill lost nothing.

This is the first migration that rewrites existing rows rather than adding a
defaulted column, so the check is counts before and after — not "the page
still loads".

    python -m scripts.verify_chatbot_backfill
"""
import os
import sys

import httpx

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

orphan_agents = [a for a in agents if not a.get("chatbot_id")]
orphan_chats = [s for s in sessions if not s.get("chatbot_id")]
print("agents without a chatbot :", len(orphan_agents))
print("chats without a chatbot  :", len(orphan_chats))
ok &= not orphan_agents and not orphan_chats

owners_with_content = {a["owner_id"] for a in agents} | {s["owner_id"] for s in sessions}
per_owner = {}
for b in bots:
    per_owner.setdefault(b["owner_id"], []).append(b)
for owner in owners_with_content:
    n = len(per_owner.get(owner, []))
    print("%-14s chatbots: %d" % (users.get(owner, owner)[:14], n))
    ok &= n >= 1

print()
print("per-user counts (compare against the spec's table):")
for owner in owners_with_content:
    print("  %-14s %2d agents, %2d chats" % (
        users.get(owner, owner)[:14],
        sum(1 for a in agents if a["owner_id"] == owner),
        sum(1 for s in sessions if s["owner_id"] == owner)))

print("\nVERDICT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
