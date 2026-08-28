# Real-browser checks

`tools/domtest` runs under jsdom, which has no layout engine and no real
cross-origin model. That leaves the embed widget's central claim untested: a
host page's CSS must not reach inside it. The shadow root exists for exactly
that, and only a rendering engine can say whether it held.

This drives the **installed** Chrome — no browser download — against a
deliberately hostile host page that embeds the **deployed** widget.

```bash
cd tools/browser
WIDGET_BASE=https://<public-url> WIDGET_TOKEN=<share-token> node widget-browser.mjs
CHAT=1 …                     # also send a real message (spends one of the daily allowance)
CHROME_PATH=… HOST_PORT=…    # if Chrome is elsewhere, or 4173 is taken
```

`puppeteer-core` is needed and, like jsdom, is installed outside the repo:

```bash
cd ~/.cache/ragchat-domtest && npm install puppeteer-core
cp <repo>/tools/browser/*.mjs . && node widget-browser.mjs
```

## Two deliberate choices

**It points at the deployed site, not localhost.** Local and deployed share one
Powabase project, and booting the app locally re-syncs the shared orchestrator
agent — so running these against a local backend would change the live demo.
Pointing at the deployment also makes the origin split real rather than a
same-machine, different-port stand-in.

**It dispatches events inside the frame rather than clicking.** `frame.click`
computes viewport coordinates, and the panel is a cross-origin iframe inside a
shadow root, where that maths does not land on the element. The first version
of the chat check failed for exactly this reason while the widget was healthy
the whole time. It is also why the × button is still not covered here.

## The one that matters

The host page sets `iframe { border: 6px dashed red !important }`. Inside the
shadow root that must compute to `0px none`. The harness negative-controls this **on every run**, rather than leaving it as
a claim: it injects the same rule inside the shadow root, asserts the reading
flips to `6px dashed rgb(255, 0, 0)`, then removes it and asserts it flips
back. So a pass means the boundary held, rather than the assertion being one
that could never fail.
