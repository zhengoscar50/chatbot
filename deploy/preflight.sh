#!/usr/bin/env bash
# Everything that must be green before deploying, in one command.
#
# This exists because twice a fully green suite sat over something that could
# not run at all — a missing CORS middleware, and a server left on stale code —
# and both were found by a person using the app, not by the tests. Unit tests
# answer "is the logic right". They do not answer "does the thing that just
# got committed actually run", which is why the deployed check below is not
# optional garnish.
#
#   ./preflight.sh                 # local checks only
#   ./preflight.sh <public-url>    # also verify what is CURRENTLY deployed
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
URL="${1:-}"
fail=0
step() { printf '\n=== %s ===\n' "$1"; }

step "backend tests"
if ( cd "$REPO/backend" && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -2 ); then
  :
else
  echo "  ^ backend tests FAILED"; fail=1
fi

step "DOM checks"
"$REPO/tools/domtest/run-all.sh" || fail=1

step "uncommitted changes"
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
  git -C "$REPO" status --short
  echo "  ^ deploying pulls from the remote, so anything uncommitted will NOT ship"
  fail=1
else
  echo "  clean"
fi

step "local commits not pushed"
if [ -n "$(git -C "$REPO" log --branches --not --remotes --oneline)" ]; then
  git -C "$REPO" log --branches --not --remotes --oneline
  echo "  ^ the box pulls from the remote; these would be left behind"
  fail=1
else
  echo "  everything is pushed"
fi

if [ -n "$URL" ]; then
  step "what is deployed right now at $URL"
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL/" || echo 000)
  echo "  site        HTTP $code"
  [ "$code" = "200" ] || fail=1
  # A route added after the running process started answers from StaticFiles
  # instead of the router — a 404/405 here, not the 401 an auth-gated route
  # gives. That is exactly the stale-server failure this check exists for.
  inbox=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL/chatbots/x/inbox" || echo 000)
  echo "  inbox route HTTP $inbox  (401 = mounted; 404/405 = stale server)"
  [ "$inbox" = "401" ] || fail=1
fi

echo
if [ "$fail" -ne 0 ]; then echo "PREFLIGHT FAILED — do not deploy."; exit 1; fi
echo "Preflight passed."
