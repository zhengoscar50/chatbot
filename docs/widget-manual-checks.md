# Embed widget — manual verification

167 automated checks pass for the embed widget, but they all run under jsdom.
jsdom has no layout engine and no real cross-origin model. It cannot tell you
whether the tab is visible, whether the panel slides, or whether a host
page's CSS reaches inside the widget and wrecks it — and CSS collision is the
specific failure the widget's shadow root exists to prevent. A page with
hostile CSS is the only real test of that boundary.

That is not a gap to be embarrassed about; it is the nature of a cross-origin,
shadow-DOM widget. But it means **the checks below are the only evidence the
widget actually works on someone else's site**, and until a person fills them
in, the honest status is "built, not verified".

## How to run it

Start the backend (serves the widget, the share endpoint, and the chat API):

```bash
cd backend && set -a && . ./.env && set +a && \
  .venv/bin/python -m uvicorn app.main:app --port 8000
```

Serve the hostile test host on a different origin (a second, unrelated site
that embeds the widget — this is what makes it a real cross-origin test):

```bash
cd /Users/oscar/Downloads/embed-test && python3 -m http.server 4173
```

Then open <http://localhost:4173/> in a browser. That page is deliberately
styled to fight the widget — `box-sizing: content-box !important`, forced
`div` margins, `button { all: unset !important; font-size: 30px !important; }`,
and a red dashed `iframe` border — all `!important`, all the kind of rules a
real host page carries by accident. The widget must survive them.

**Which token to use:** the host page already embeds a live share token for
the `My chatbot` chatbot (`2cv9l8i9qu04RhWek7mytGBgMM3TH4tKDNdxKSDkHVE`),
pointed at `http://localhost:8000`. No login is needed to use the widget
itself — that is the point of a share link. To re-fetch the token or confirm
it's still live:

```bash
cd backend && set -a && . ./.env && set +a && .venv/bin/python -c "
import os, httpx
B=os.environ['POWABASE_BASE_URL'].rstrip('/'); K=os.environ['POWABASE_SERVICE_ROLE_KEY']
r=httpx.get(B+'/rest/v1/chatbots', params={'select':'name,share_token'}, headers={'apikey':K,'Authorization':'Bearer '+K}, timeout=30).json()
print([x for x in r if x.get('share_token')])"
```

## The checks

Fill in the verdict column by actually looking. Leave a row blank rather than
guessing — a blank row is information; a guessed tick is not.

| Check | Why no test can reach it | Verdict |
|---|---|---|
| The tab is visible on the right edge and is not 30px tall | jsdom has no layout; this is the hostile-CSS check | |
| Clicking slides the panel out rather than snapping | No layout, no transitions | |
| The chat inside works — ask something, get an answer | Real cross-origin fetch | |
| The × inside the panel closes it | Real postMessage across real origins | |
| Reloading the host page reopens the panel with the thread intact | Real storage, real transcript endpoint | |
| Navigating to another page on the host keeps the thread | The reason persistence exists | |
| The iframe does not have a red dashed border | Proves host CSS did not reach inside the shadow root | |
| At a 380px window the tab is a bottom pill and the panel is full width | Layout | |
| Both themes are legible | Colour rendering | |

## Verified without a browser

These were checked programmatically with curl against the running backend and
need no human pass:

- `GET http://localhost:8000/widget.js` → `200 OK`, `content-type: text/javascript; charset=utf-8`
  (not `text/plain` — a wrong content-type would make browsers refuse to
  execute the script, and no jsdom test can catch that).
- `GET http://localhost:8000/widget.css.js` → `200 OK`, `content-type: text/javascript; charset=utf-8`.
- `GET http://localhost:8000/s/<token>?embed=1` → `200 OK`,
  `content-type: text/html; charset=utf-8`.
- `GET http://localhost:4173/` (the host page) → `200 OK`, and the response
  body contains `<script src="http://localhost:8000/widget.js" ...>`.
- 167 automated checks pass across the existing runners (unchanged by this
  task).

Exact command output is recorded in
`.superpowers/sdd/2026-08-25-embed-widget/task-7-report.md`.

## Still unverified after this document is filled in

- Any browser other than the one used to fill in the table above.
- Touch devices — every interaction assumed here is a pointer (click, hover).
- Screen readers — nothing here confirms the tab or panel are announced
  sensibly, or that focus moves correctly when the panel opens and closes.
- Real third-party hosting (a different machine, a different network) — this
  document only proves the widget survives a same-machine, different-port
  origin split, which is a real but limited stand-in for a genuinely
  different site.
