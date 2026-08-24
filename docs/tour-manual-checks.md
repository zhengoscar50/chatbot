# Guided tour — manual verification

Automated tests cannot verify this feature's core. jsdom has no layout engine,
so `tools/domtest/tour.mjs` stubs `getBoundingClientRect` to make the engine's
logic run at all. Every geometric claim — that the dim panes align to the
highlighted control, that the control is genuinely clickable, that the box does
not cover what it describes — is unverified by any test in this repo.

That is not a gap to be embarrassed about; it is the nature of a spotlight. But
it means **the checks below are the only evidence this feature works**, and
until someone fills them in, the honest status is "built, not verified".

Both user-visible bugs in this app so far — the `.app` cascade bug and the
permanently-open card menus — were layout bugs that every automated check
passed and a person caught in seconds.

## How to run it

```bash
cd backend && set -a && . ./.env && set +a && \
  .venv/bin/python -m uvicorn app.main:app --port 8000
```

Then open <http://localhost:8000> and press the **?** button in the dashboard
header (left of the theme toggle).

**Which account you use changes what you see**, because each step reads its
`done` flag from `GET /onboarding`:

| Account | Steps done | The tour will |
|---|---|---|
| `oscarzheng` | 5/5 | run entirely in *showing* mode — captions, no instructions |
| `oscar` | 4/5 | tell you to add a document, narrate the rest |
| `zheng` | 1/5 | give the full guided arc |
| a fresh signup | 1/5 | autoplay on first arrival at the dashboard |

To see the guided version rather than the narrated one, use `zheng` or sign up.

## The checks

Fill in the verdict column by actually looking. Leave a row blank rather than
guessing — a blank row is information; a wrong tick is not.

### Geometry — the part no test can reach

| # | Check | Verdict |
|---|---|---|
| 1 | The four dim panes meet flush around the highlighted control — no seam, no gap, no sliver of undimmed page | |
| 2 | The highlighted control is genuinely clickable, and clicking just outside it does nothing | |
| 3 | The box never covers the control it is describing, on any of the eight steps | |
| 4 | Scrolling mid-step keeps the panes locked to the target | |
| 5 | Resizing the window re-aligns the panes | |

### Both themes

| # | Check | Verdict |
|---|---|---|
| 6 | Light: the box is legible against the dimmed page | |
| 7 | Dark: same, and the dim does not read as a black hole | |
| 8 | The Next button's contrast is acceptable in both | |

### Narrow window

| # | Check | Verdict |
|---|---|---|
| 9 | At ≤400px wide the box docks to the bottom and does not overflow | |
| 10 | At a very short height the box scrolls internally and **Skip stays reachable** | |

### Keyboard and focus

| # | Check | Verdict |
|---|---|---|
| 11 | Escape ends the tour from every step, on every surface | |
| 12 | Tab from the box reaches the highlighted control | |
| 13 | The step text is announced when a step changes (VoiceOver or similar) | |

### The arc itself

| # | Check | Verdict |
|---|---|---|
| 14 | All eight steps, as a fresh signup, end to end | |
| 15 | Cancelling the chatbot-name prompt leaves the step standing rather than advancing | |
| 16 | Walking back to the dashboard mid-step rewinds instead of hanging | |
| 17 | Step 8 with a **general assistant** answer shows the fallback and its button returns to step 5 | |
| 18 | Skipping, then pressing **?** again, replays from the first incomplete step | |

## Verified without a browser

These were checked programmatically and need no human pass:

- `tour.js`, `tour-steps.js`, `tour-spotlight.js` are served (HTTP 200) and the
  tour markup is present in the delivered HTML.
- The `?` button's accessible name is "Take the tour".
- 127 automated checks pass across seven runners.
- Seven engine mutations are each caught by a named check.

## Still unverified after this document is filled in

- Any browser other than the one used.
- Touch devices — every interaction here assumes a pointer.
- Screen readers other than the one tried, if any.
