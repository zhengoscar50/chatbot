#!/usr/bin/env bash
# What URL is the demo on, is it actually serving, and has it moved?
#
# A quick tunnel's hostname is regenerated whenever cloudflared restarts, which
# silently breaks every embed snippet already pasted onto another site. Nothing
# reports that today: the service is healthy, the app is healthy, and the only
# broken thing is somebody else's page.
#
# Run it after any restart or reboot, or from cron to get a record of when the
# address moved.
#
#   ./tunnel-status.sh          # report
#   ./tunnel-status.sh --quiet  # exit status only: 0 unchanged, 3 moved, 1 broken
set -uo pipefail

STATE="${TUNNEL_STATE_FILE:-$HOME/.cache/tunnel-url}"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1
say() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }

if systemctl show cloudflared -p ExecStart --value 2>/dev/null | grep -q -- '--url'; then
  VARIANT="quick tunnel (hostname changes on every restart)"
else
  VARIANT="named tunnel (hostname is stable)"
fi

# Only the CURRENT run's URL counts. The journal holds every URL this box has
# ever been given, so reading the last line overall would happily report a
# hostname from three restarts ago as if it were live.
SINCE=$(systemctl show cloudflared -p ActiveEnterTimestamp --value)
URL=$(journalctl -u cloudflared --since "${SINCE:--1 hour}" --no-pager 2>/dev/null \
      | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)

if [ -z "$URL" ]; then
  # A named tunnel never prints one; its hostname lives in the dashboard.
  say "variant : $VARIANT"
  say "url     : not in this run's logs (expected for a named tunnel)"
  [ "$VARIANT" = "${VARIANT#quick}" ] && exit 0
  say "STATUS  : BROKEN — a quick tunnel with no URL in its own logs"
  exit 1
fi

say "variant : $VARIANT"
say "url     : $URL"

CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL/" || echo 000)
say "serving : HTTP $CODE"

PREV=""
[ -f "$STATE" ] && PREV=$(cat "$STATE")
mkdir -p "$(dirname "$STATE")" && printf '%s' "$URL" > "$STATE"

if [ "$CODE" != "200" ]; then
  say "STATUS  : BROKEN — the tunnel is up but the app did not answer through it"
  exit 1
fi

if [ -n "$PREV" ] && [ "$PREV" != "$URL" ]; then
  say ""
  say "STATUS  : MOVED — was $PREV"
  say "          Every embed already pasted on another site is now dead."
  say "          Re-copy the snippet from Share on the chatbot card, or switch"
  say "          to a named tunnel (deploy/README.md) so this stops happening."
  exit 3
fi

say "STATUS  : OK${PREV:+ — unchanged since last check}"
exit 0
