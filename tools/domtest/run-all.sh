#!/usr/bin/env bash
# Run every DOM check, from a harness that does not evaporate.
#
# The repo keeps no dependency manifest, so jsdom cannot live in it. It used to
# be installed by hand in /tmp, which meant the checks guarding the whole
# frontend disappeared on the next reboot and nothing said so — you found out
# by not running them. This keeps that same "outside the repo" arrangement but
# puts it somewhere durable, and creates it on demand if it is missing.
#
#   ./run-all.sh          # every check
#   ./run-all.sh inbox    # just inbox.mjs
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
HOME_DIR="${RAGCHAT_DOMTEST_HOME:-$HOME/.cache/ragchat-domtest}"

export RAGCHAT_FE="$REPO/frontend"

if [ ! -d "$HOME_DIR/node_modules/jsdom" ]; then
  echo "Setting up the DOM harness in $HOME_DIR (one time)…"
  mkdir -p "$HOME_DIR" || exit 1
  ( cd "$HOME_DIR" \
    && { [ -f package.json ] || npm init -y >/dev/null 2>&1; } \
    && npm install jsdom >/dev/null 2>&1 ) || {
      echo "FAILED: could not install jsdom into $HOME_DIR (network?)." >&2
      exit 1
    }
fi

# Copied rather than symlinked: node resolves node_modules by walking up from
# the script's own directory, so the scripts have to sit beside it.
cp "$HERE"/*.mjs "$HOME_DIR"/ || exit 1

if [ "$#" -gt 0 ]; then
  SCRIPTS=()
  for name in "$@"; do SCRIPTS+=("${name%.mjs}.mjs"); done
else
  SCRIPTS=(run.mjs scope.mjs onboarding.mjs tour.mjs tour_steps.mjs \
           tour_geometry.mjs share_citations.mjs share_modal.mjs \
           widget.mjs inbox.mjs snippet.mjs)
fi

failed=()
for s in "${SCRIPTS[@]}"; do
  if [ ! -f "$HOME_DIR/$s" ]; then
    echo "  MISSING  $s"; failed+=("$s"); continue
  fi
  line=$( cd "$HOME_DIR" && node "$s" 2>&1 | grep -E "checks passed|passed, 0 FAILED|FAILED" | tail -1 )
  status=$?
  # grep's status is not node's; re-run cheaply is wasteful, so judge by output.
  if printf '%s' "$line" | grep -q "0 FAILED\|checks passed"; then
    printf "  %-22s %s\n" "$s" "$line"
  else
    printf "  %-22s %s\n" "$s" "${line:-no summary line — the script threw}"
    failed+=("$s")
  fi
done

echo
if [ "${#failed[@]}" -gt 0 ]; then
  echo "FAILED: ${failed[*]}"
  exit 1
fi
echo "All DOM checks passed."
