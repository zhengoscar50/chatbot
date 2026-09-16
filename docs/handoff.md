# Handoff — state, verification, and the traps

What a person needs to pick this up cold. The README covers setup; this covers
running it, checking it, and the mistakes that already cost time once.

Last verified: 2026-09-15.

## How to check it is working

Four commands, cheapest first. Each answers a question the one above it cannot.

```bash
# 1. Logic. Fast, offline, proves nothing about the real world.
cd backend && .venv/bin/python -m pytest tests/ -q          # 649 tests

# 2. The frontend, under jsdom. No layout engine, no real cross-origin.
./tools/domtest/run-all.sh                                  # 241 checks, 11 scripts

# 3. Before deploying: the above, plus uncommitted/unpushed work, plus what is
#    live right now.
./deploy/preflight.sh https://<public-url>

# 4. On the box, after any deploy touching a query. THE important one.
cd ~/rag-chatbot/backend && set -a && . ./.env && set +a
.venv/bin/python ../deploy/smoke.py --token <a-share-token>  # 17 checks
```

Optional, needs Chrome and `npm install puppeteer-core` outside the repo:

```bash
cd tools/browser
WIDGET_BASE=https://<public-url> WIDGET_TOKEN=<share-token> node widget-browser.mjs
```

### Why there are four and not one

Each layer is blind to the one below it.

**Unit tests cannot see the database.** A query once selected `text` from a
table whose column is `content`. It passed 649 tests, nine mutation checks and
the preflight gate, because the fakes were written by the same person as the
query and agreed with it about a column neither had checked. The owner found it
on the first real click. `tests/unit/test_schema_agreement.py` now parses the
migrations and rejects any `select` naming a column that does not exist, and
`deploy/smoke.py` exercises the real routes against the real database.

**preflight cannot see inside a route.** A 401 from an auth-gated route looks
identical whether the query behind it works or not. That is exactly how the bug
above survived a green preflight.

**jsdom cannot see layout.** It has no layout engine and no real cross-origin
model, so it cannot tell you whether the widget is visible or whether a host
page's CSS reached inside it. `tools/browser/` covers that in real Chrome.

The principle worth keeping: **fakes agree with you; real data does not.**

## Known issues

### The tunnel hostname is not stable

`cloudflared` runs as a *quick tunnel*, so the public hostname is regenerated on
every restart or reboot — and every embed snippet already pasted on someone
else's site breaks when that happens. The share token does not change, so
re-copying the snippet from **Share** fixes it.

`./deploy/tunnel-status.sh` reports the current URL, checks the app answers
*through* it, and says when it has moved (exit 0 unchanged, 3 moved, 1 broken).

The permanent fix is a named tunnel, and everything on the machine side is
ready for it: the binary is at `/usr/local/bin/cloudflared`, `deploy/
cloudflared.service` works as-is, and the app needs no configuration change
(share URLs are built from `request.base_url`, and uvicorn honours the
`X-Forwarded-Proto` and `Host` headers cloudflared sends). It needs a domain on
a Cloudflare account and a tunnel token. See "Switching a box that already runs
the quick tunnel" in `deploy/README.md` — the ordinary steps do NOT work on a
box already running the quick variant.

### Local development is not isolated from production

Local and deployed share one Powabase project. The orchestrator and general
assistant are single shared agents whose model is re-synced from `.env` at every
startup, so **starting a local server rewrites the live demo's router**. Keep the
model variables identical in both `.env` files, and prefer pointing test tooling
at the deployed URL rather than booting the app locally — which is why
`tools/browser/widget-browser.mjs` does exactly that.

### Redaction hides filenames, not topics

A stranger on a share link never sees a document's filename: citations are
relabelled "Source 1", and `SourceNameIndex` scrubs any of the chatbot's
document names out of the answer prose. But a model that writes "the HR
handbook" while the file is `05_hr-handbook.pdf` still conveys the topic.
Nothing can close that without mangling ordinary sentences.

The scrubbing rule is deliberately asymmetric for the same reason: a full
filename is always removed, a bare stem only when it could not plausibly be an
ordinary word. Missing a leak is rare and quiet; turning every "pricing" into
"a document" happens on every turn and the visitor reads it.

### First visitor message per chatbot is slower

The document-name lookup spans every knowledge base in scope — about 0.7s cold
across 11 KBs, 0.06s warm. The cache is invalidated exactly, from
`PowabaseClient.add_source_to_kb`: every document that becomes answerable
passes through that one method, so no upload path can add one the index then
fails to re-read. A TTL backstops documents added through the Powabase
dashboard, which fires no hook here.

### The daily-cap race

Two simultaneous requests can both read the same count and both proceed. At
this scale that costs one extra message, not a breach. Documented as accepted
in `share_service.consume`.

## Not done, deliberately

- `docs/tour-manual-checks.md` — 18 rows still blank. Now clearly automatable
  the way the widget's were, and easier, since the tour is same-origin.
- Three widget rows need human eyes: whether the panel *slides* rather than
  snaps, the × click in a real browser (coordinate clicks do not land inside a
  cross-origin iframe nested in a shadow root), and whether it looks good.
- Per-site token revocation. A share token is all-or-nothing: revoking it
  breaks every site using it.
- Streaming. Answers arrive whole.

## If the chatbot stops answering

Check which layer is failing before changing anything.

```bash
curl -s https://<public-url>/                       # the app itself
curl -s https://<public-url>/s/<token>/info         # the app -> Powabase
```

"Upstream service unavailable" from the second means the app is healthy and
**Powabase** is not — check the project in the Powabase dashboard. As of
2026-09-15 both Powabase layers (`/rest/v1/` and `/api/`) were returning 502
"An invalid response was received from the upstream server", which makes the
chatbot non-functional while every route in this repo continues to behave
correctly. Nothing here can fix that; it is upstream.
