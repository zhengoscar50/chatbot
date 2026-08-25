# Embed Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the share iframe snippet into a one-line script tag that puts a slim tab on the edge of someone else's website and slides a chat panel out of it.

**Architecture:** A loader script (`widget.js`) appends one element with an open shadow root to the host page, containing a tab button and a panel that holds an `<iframe>` pointed at the existing `/s/<token>` page. The loader owns the visitor's session — it creates one on first open and passes the id in the iframe URL fragment, so no identifier is ever `postMessage`d to a frame whose origin cannot be verified. Resuming a conversation needs one new backend endpoint that replays a transcript through the same redaction the live answer path uses.

**Tech Stack:** Vanilla JS with no build step and no module system; FastAPI + pytest on the backend; jsdom via `tools/domtest/` for DOM behaviour.

**Spec:** `docs/superpowers/specs/2026-08-25-embed-widget-design.md`

## Global Constraints

- **No module system, no build step.** `frontend/*.js` are plain `<script src>` files. `widget.js` is the exception in one respect only: it is loaded by *other people's* sites, so it must not assume any of this project's other scripts exist. It declares nothing on `window` beyond a single guard flag.
- **The chat itself never renders in the host page.** The panel's contents stay in an `<iframe>` on our origin.
- **Shadow DOM is required**, `mode: "open"`. Every style the widget needs goes inside that root; no rule is added to the host document.
- **The loader owns the session.** It creates one on first open, stores the id, and passes it via the iframe URL fragment. The panel never posts a session id out.
- **Exactly two messages cross the frame boundary**, both panel → loader: `{source:"powabase-widget", type:"ready"}` and `{source:"powabase-widget", type:"close"}`. The loader drops any message whose `event.origin` is not its own script's origin, and any message lacking that exact `source`.
- **No session is created on page load.** Only on first open.
- **`localStorage` keys are namespaced by token:** `powabase-widget:<token>:session` and `powabase-widget:<token>:open`. Every read and write wrapped in try/catch.
- **The transcript endpoint returns 404, never 403**, on both failure modes — a 403 would confirm a session exists.
- **Every assistant turn replayed by that endpoint goes through `redact_turn`.** Stored rows are unredacted: `share.py:127` redacts the response after `answer_turn` has written the row.
- **Run backend tests:** `cd backend && .venv/bin/python -m pytest tests/ -q` (must stay at 565+ passed).
- **Run DOM tests:** `cd /tmp/domtest && cp <repo>/frontend/*.js . && cp <repo>/tools/domtest/*.mjs . && node <runner>.mjs`
- **Never assert visibility with `getComputedStyle`** — jsdom special-cases `hidden` and such an assertion cannot fail. See `tools/domtest/README.md`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/api/routes/share.py` (modify) | One new route: the transcript replay, redacted. |
| `backend/tests/unit/test_routes_share.py` (modify) | Its two 404s and its redaction. |
| `frontend/widget.js` (create) | The loader: shadow root, tab, panel, session ownership, persistence, message handling. Self-contained — assumes nothing else on the host page. |
| `frontend/widget.css.js` (create) | The widget's stylesheet as an exported string, injected into the shadow root. Split out so the loader stays readable and the styles can be reviewed as styles. |
| `frontend/share.js` (modify) | Accept a session id from the URL fragment; in embed mode, replay the transcript and offer a close control. |
| `frontend/share.html` (modify) | The close button, hidden unless embedded. |
| `backend/app/api/routes/chatbots.py` (modify) | The Share dialog hands out the script tag alongside the iframe. |
| `tools/domtest/widget.mjs` (create) | The loader's behaviour. |

Task 1 is backend-only and testable alone. Tasks 2-3 build the loader. Task 4 makes the panel cooperate. Task 5 wires the snippet into the dialog. Task 6 tests the assembled whole cross-origin by hand.

---

### Task 1: The transcript endpoint

**Files:**
- Modify: `backend/app/api/routes/share.py`
- Test: `backend/tests/unit/test_routes_share.py`

**Interfaces:**
- Consumes: `redact_turn(answer, citations) -> tuple` from `app.services.share_service`; `_chatbot_or_404(share, token)` already in `share.py`.
- Produces: `GET /s/{token}/session/{session_id}/messages` → `{"messages": [{"role": str, "content": str, "citations": list, "answered_by": {"name": str} | None}]}`.

**Context:** `public_chat` in this file already performs the check this route must repeat — read it first. The owner's own private chats live in the same chatbot, so chatbot membership alone is not sufficient; the session must also carry `shared`. `MessageStore.list(session_id)` returns rows oldest-first with `role`, `content`, `citations`, `answered_by_name`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_routes_share.py`:

```python
def test_transcript_replays_a_visitors_own_conversation():
    app, fakes = build_app()
    fakes.chatbots.rows.append({
        "id": "cb1", "owner_id": "o1", "name": "Bot",
        "share_token": "tok", "share_daily_limit": 10,
    })
    fakes.sessions.rows["s1"] = {"id": "s1", "chatbot_id": "cb1", "shared": True}
    fakes.messages.rows["s1"] = [
        {"role": "user", "content": "hi", "citations": [], "answered_by_name": None},
        {"role": "assistant", "content": "hello", "citations": [],
         "answered_by_name": "Chem Tutor"},
    ]

    body = TestClient(app).get("/s/tok/session/s1/messages").json()

    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["content"] == "hello"
    assert body["messages"][1]["answered_by"] == {"name": "Chem Tutor"}


def test_transcript_refuses_a_session_from_another_chatbot():
    """Enumeration guard. A visitor holding one chatbot's token must not be
    able to read a conversation belonging to a different chatbot."""
    app, fakes = build_app()
    fakes.chatbots.rows.append({
        "id": "cb1", "owner_id": "o1", "name": "Bot",
        "share_token": "tok", "share_daily_limit": 10,
    })
    fakes.sessions.rows["other"] = {"id": "other", "chatbot_id": "cb2", "shared": True}

    assert TestClient(app).get("/s/tok/session/other/messages").status_code == 404


def test_transcript_refuses_the_owners_private_chat():
    """The owner's own chats live in this same chatbot. Membership alone is not
    enough — this is the check public_chat already makes, and the reason it
    makes it."""
    app, fakes = build_app()
    fakes.chatbots.rows.append({
        "id": "cb1", "owner_id": "o1", "name": "Bot",
        "share_token": "tok", "share_daily_limit": 10,
    })
    fakes.sessions.rows["priv"] = {"id": "priv", "chatbot_id": "cb1", "shared": False}

    assert TestClient(app).get("/s/tok/session/priv/messages").status_code == 404


def test_transcript_is_404_not_403_for_a_session_that_does_not_exist():
    """403 would confirm the session exists, which is exactly what an
    enumeration attempt wants to learn."""
    app, fakes = build_app()
    fakes.chatbots.rows.append({
        "id": "cb1", "owner_id": "o1", "name": "Bot",
        "share_token": "tok", "share_daily_limit": 10,
    })

    assert TestClient(app).get("/s/tok/session/nope/messages").status_code == 404


def test_transcript_redacts_filenames_the_live_answer_also_hides():
    """The check worth writing first. Rows are stored UNREDACTED — share.py
    redacts the response after answer_turn has written the row — so replaying
    them raw hands back the document names the live path stripped. The same
    answer would be secret when given and public when read back."""
    app, fakes = build_app()
    fakes.chatbots.rows.append({
        "id": "cb1", "owner_id": "o1", "name": "Bot",
        "share_token": "tok", "share_daily_limit": 10,
    })
    fakes.sessions.rows["s1"] = {"id": "s1", "chatbot_id": "cb1", "shared": True}
    fakes.messages.rows["s1"] = [{
        "role": "assistant",
        "content": "According to Q3-finances.pdf, revenue rose.",
        "citations": [{"source_name": "Q3-finances.pdf", "source_id": "src-1",
                       "excerpt": "revenue rose 4%"}],
        "answered_by_name": "Analyst",
    }]

    body = TestClient(app).get("/s/tok/session/s1/messages").json()
    turn = body["messages"][0]

    assert "Q3-finances.pdf" not in turn["content"]
    assert "Q3-finances.pdf" not in str(turn["citations"])
    assert "src-1" not in str(turn["citations"])
    # The excerpt is what makes an answer credible and is deliberately kept.
    assert "revenue rose 4%" in str(turn["citations"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_routes_share.py -q -k transcript`

Expected: all five fail with 404 from an unregistered route (FastAPI returns 404 for an unknown path, so read the failure text — the redaction and replay tests must fail on their *content* assertions once the route exists, not merely on status).

**If `build_app()` in this file does not already expose `fakes.messages` with a `rows` dict, add it** following the shape of the existing fakes, and say so in your report.

- [ ] **Step 3: Write the route**

In `backend/app/api/routes/share.py`, after `public_session` and before `public_chat`:

```python
@router.get("/{token}/session/{session_id}/messages")
async def public_transcript(
    token: str,
    session_id: str,
    share: ShareService = Depends(get_share_service),
    sessions: SessionService = Depends(get_session_service),
    messages: MessageStore = Depends(get_message_store),
):
    """A visitor's own conversation, replayed so the widget can resume it.

    The same BOTH-conditions check public_chat makes, for the same reason: the
    owner's private chats live in this chatbot too, so membership alone would
    let a visitor name one and read it.

    404 on every failure, never 403 — a 403 confirms the session exists, which
    is the one fact an enumeration attempt is after.
    """
    chatbot = await run_in_threadpool(_chatbot_or_404, share, token)
    session_row = await run_in_threadpool(sessions.get, session_id)
    if (session_row is None
            or session_row.get("chatbot_id") != chatbot["id"]
            or not session_row.get("shared")):
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    rows = await run_in_threadpool(messages.list, session_id)
    out = []
    for row in rows:
        content = row.get("content") or ""
        citations = row.get("citations") or []
        # Stored rows are unredacted: share.py redacts the RESPONSE, after
        # answer_turn has already written the row. Replaying raw would hand
        # back the filenames the live answer stripped.
        if row.get("role") == "assistant":
            content, citations = redact_turn(content, citations)
        name = row.get("answered_by_name")
        out.append({
            "role": row.get("role"),
            "content": content,
            "citations": citations,
            "answered_by": {"name": name} if name else None,
        })
    return {"messages": out}
```

Add `MessageStore, get_message_store` to the imports from `app.services.message_store` if not already present.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_routes_share.py -q`

Expected: all pass.

- [ ] **Step 5: Mutation-check the three that carry the security**

Make each edit, confirm the *named* test fails, then **revert**.

1. Drop `or not session_row.get("shared")` → `test_transcript_refuses_the_owners_private_chat` must FAIL.
2. Drop `or session_row.get("chatbot_id") != chatbot["id"]` → `test_transcript_refuses_a_session_from_another_chatbot` must FAIL.
3. Remove the `if row.get("role") == "assistant":` redaction block → `test_transcript_redacts_filenames_the_live_answer_also_hides` must FAIL.

If any mutation leaves the suite green, that test is not reaching the code it names — report it rather than adjusting the assertion.

- [ ] **Step 6: Run the whole suite and commit**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
git add backend/app/api/routes/share.py backend/tests/unit/test_routes_share.py
git commit -m "feat: replay a visitor's transcript, redacted"
```

---

### Task 2: The widget's stylesheet

**Files:**
- Create: `frontend/widget.css.js`

**Interfaces:**
- Consumes: nothing.
- Produces: a global `WIDGET_CSS` — a template string of CSS, and `widgetVars(accent)` returning a `:host` custom-property block for the one configurable colour.

**Context:** this is injected into a shadow root, so it styles only the widget. It must not reference any class from `styles.css`, because the host page has never loaded it. It also cannot rely on inherited values — a host page may set anything on `body`.

- [ ] **Step 1: Write the stylesheet**

Create `frontend/widget.css.js`:

```javascript
// The widget's styles, as a string injected into its shadow root.
//
// Kept apart from the loader so the styles can be read as styles. Everything
// here is scoped by the shadow boundary, so class names are short and cannot
// collide with the host page — and nothing here escapes to touch their layout.
//
// Nothing inherits. A host page may set any font, colour or box-sizing on body,
// and inherited values cross a shadow boundary, so every property the widget
// depends on is stated explicitly.

function widgetVars(accent) {
  return `:host{--w-accent:${accent};}`;
}

const WIDGET_CSS = `
:host{
  all: initial;
  --w-bg: #ffffff;
  --w-ink: #16171a;
  --w-line: #e3e4e8;
  --w-tab-w: 34px;
  --w-panel-w: 400px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
*{box-sizing:border-box;}

.wrap{
  position: fixed;
  top: 0;
  bottom: 0;
  z-index: 2147483000;   /* below the max, so a host modal can still win */
  display: flex;
  align-items: center;
  pointer-events: none;  /* the gap between tab and panel stays clickable */
}
.wrap[data-side="right"]{ right: 0; flex-direction: row; }
.wrap[data-side="left"]{ left: 0; flex-direction: row-reverse; }

.tab{
  pointer-events: auto;
  flex: none;
  width: var(--w-tab-w);
  padding: 14px 0;
  border: 0;
  border-radius: 8px 0 0 8px;
  background: var(--w-accent);
  color: #fff;
  font: 600 12px/1 inherit;
  letter-spacing: .08em;
  text-transform: uppercase;
  writing-mode: vertical-rl;
  cursor: pointer;
  box-shadow: 0 2px 14px rgba(0,0,0,.18);
}
.wrap[data-side="left"] .tab{ border-radius: 0 8px 8px 0; transform: rotate(180deg); }
.tab:focus-visible{ outline: 2px solid #fff; outline-offset: -4px; }

.panel{
  pointer-events: auto;
  width: var(--w-panel-w);
  max-width: 100vw;
  height: 100%;
  background: var(--w-bg);
  border-left: 1px solid var(--w-line);
  box-shadow: -8px 0 30px rgba(0,0,0,.18);
  transform: translateX(100%);
  transition: transform 220ms ease;
}
.wrap[data-side="left"] .panel{
  border-left: 0;
  border-right: 1px solid var(--w-line);
  box-shadow: 8px 0 30px rgba(0,0,0,.18);
  transform: translateX(-100%);
}
.wrap[data-open] .panel{ transform: translateX(0); }

.panel iframe{ display:block; width:100%; height:100%; border:0; }

@media (max-width: 640px){
  :host{ --w-panel-w: 100vw; }
  .wrap{ align-items: flex-end; }
  .tab{
    writing-mode: horizontal-tb;
    width: auto;
    padding: 10px 16px;
    margin: 0 0 16px 0;
    border-radius: 999px;
  }
  .wrap[data-side="left"] .tab{ transform: none; border-radius: 999px; }
  .wrap[data-open] .tab{ display: none; }  /* a full-width panel owns the screen */
}

@media (prefers-reduced-motion: reduce){
  .panel{ transition: none; }
}
`;
```

- [ ] **Step 2: Check it parses as CSS**

```bash
node --check frontend/widget.css.js && python3 - <<'EOF'
import re
src = open("frontend/widget.css.js").read()
css = src[src.index("const WIDGET_CSS = `") + 20 : src.rindex("`")]
opens, closes = css.count("{"), css.count("}")
print(f"  braces balanced: {opens == closes} ({opens}/{closes})")
# Every selector must be scoped to the shadow root — no bare element or html/body rules.
bad = [l.strip() for l in css.splitlines()
       if l.strip().endswith("{") and re.match(r"^(html|body|div|button)\s*\{", l.strip())]
print(f"  host-page selectors: {bad or 'none'}")
assert opens == closes and not bad
EOF
```

Expected: balanced, no host-page selectors.

- [ ] **Step 3: Commit**

```bash
git add frontend/widget.css.js
git commit -m "feat: the embed widget's shadow-scoped stylesheet"
```

---

### Task 3: The loader

**Files:**
- Create: `frontend/widget.js`

**Interfaces:**
- Consumes: `WIDGET_CSS`, `widgetVars(accent)` (Task 2) — but see the note below, they are inlined rather than imported.
- Produces: no globals except `window.__powabaseWidget` (a boolean guard).

**Context and two constraints that shape this file:**

**It is loaded by other people's sites.** It cannot assume `widget.css.js` was also loaded, because the host pastes exactly one script tag. So the loader **fetches the CSS from its own origin** at startup: `fetch(new URL("/widget.css", scriptOrigin))`. Add a route serving `widget.css.js`'s string as `text/css`? No — simpler and with one fewer moving part: **Task 3 inlines the CSS by importing nothing and instead reading `WIDGET_CSS` from a second script tag it injects itself**, loading `/widget.css.js` from its own origin before building the UI. That keeps Task 2's file as the single source of the styles and still needs only one tag from the host.

**It must find its own origin.** `document.currentScript.src` gives it, and must be read at top level — `currentScript` is null inside async callbacks.

- [ ] **Step 1: Write the loader**

Create `frontend/widget.js`:

```javascript
// The embeddable chat widget.
//
// A host site pastes one tag:
//   <script src="https://…/widget.js" data-token="…" async></script>
//
// This file runs on THEIR page, so it assumes nothing: no other script of ours
// is loaded, no CSS of ours exists, and their globals are none of our business.
// It declares exactly one name on window, as a guard against being pasted twice.
//
// Everything it builds lives inside an open shadow root, so their stylesheet
// cannot break the tab and the tab's styles cannot reach their layout. The chat
// itself stays in an iframe on our origin — model output and document text
// should never render inside a stranger's document.

(function () {
  if (window.__powabaseWidget) return;
  window.__powabaseWidget = true;

  // Must be read synchronously: currentScript is null once we are in a callback.
  const script = document.currentScript;
  if (!script) return;
  const origin = new URL(script.src, location.href).origin;
  const token = script.getAttribute("data-token") || "";
  const side = script.getAttribute("data-side") === "left" ? "left" : "right";
  const label = script.getAttribute("data-label") || "Ask";
  const accent = script.getAttribute("data-accent") || "#3e6ae1";
  if (!token) return;

  const KEY_SESSION = `powabase-widget:${token}:session`;
  const KEY_OPEN = `powabase-widget:${token}:open`;

  // A host page may block site data entirely. That must degrade to a widget
  // that works and forgets, never to a widget that fails to appear.
  function read(key) {
    try { return localStorage.getItem(key); } catch (err) { return null; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (err) { /* forget instead */ }
  }
  function drop(key) {
    try { localStorage.removeItem(key); } catch (err) { /* nothing to do */ }
  }

  let sessionId = read(KEY_SESSION);
  let open = false;
  let loaded = false;

  const host = document.createElement("div");
  host.setAttribute("data-powabase-widget", "");
  const root = host.attachShadow({ mode: "open" });

  const wrap = document.createElement("div");
  wrap.className = "wrap";
  wrap.setAttribute("data-side", side);

  const tab = document.createElement("button");
  tab.type = "button";
  tab.className = "tab";
  tab.textContent = label;
  tab.setAttribute("aria-expanded", "false");

  const panel = document.createElement("div");
  panel.className = "panel";
  const frame = document.createElement("iframe");
  frame.title = "Chat";
  panel.appendChild(frame);

  wrap.appendChild(tab);
  wrap.appendChild(panel);

  // Styles come from our own origin so the host pastes only one tag, and so
  // widget.css.js stays the single place the styles live.
  const cssTag = document.createElement("script");
  cssTag.src = origin + "/widget.css.js";
  cssTag.onload = function () {
    const style = document.createElement("style");
    style.textContent =
      (typeof widgetVars === "function" ? widgetVars(accent) : "")
      + (typeof WIDGET_CSS === "string" ? WIDGET_CSS : "");
    root.appendChild(style);
  };
  document.head.appendChild(cssTag);

  root.appendChild(wrap);
  document.body.appendChild(host);

  // The session is created on FIRST OPEN, never on page load: a visitor who
  // never clicks should leave no empty conversation in the owner's data.
  async function ensureSession() {
    // A stored id can outlive the session it names. Check before handing it
    // over, so the panel can trust what it is given absolutely and never has
    // to invent one of its own — two things creating sessions is exactly the
    // split ownership this design exists to avoid.
    if (sessionId) {
      const check = await fetch(
        `${origin}/s/${encodeURIComponent(token)}/session/`
        + `${encodeURIComponent(sessionId)}/messages`
      );
      if (check.ok) return sessionId;
      sessionId = null;
      drop(KEY_SESSION);
    }
    const res = await fetch(`${origin}/s/${encodeURIComponent(token)}/session`, {
      method: "POST",
    });
    if (!res.ok) return null;
    sessionId = (await res.json()).session_id;
    write(KEY_SESSION, sessionId);
    return sessionId;
  }

  async function load() {
    if (loaded) return;
    loaded = true;
    const id = await ensureSession();
    // The id travels in the fragment, never through postMessage: a fragment
    // goes only to the frame we are pointing at, whereas a message would be
    // posted to a parent whose origin the panel cannot verify.
    const base = `${origin}/s/${encodeURIComponent(token)}?embed=1`;
    frame.src = id ? `${base}#session=${encodeURIComponent(id)}` : base;
  }

  function setOpen(next) {
    open = next;
    if (open) wrap.setAttribute("data-open", "");
    else wrap.removeAttribute("data-open");
    tab.setAttribute("aria-expanded", open ? "true" : "false");
    write(KEY_OPEN, open ? "1" : "0");
    if (open) load();
  }

  tab.addEventListener("click", function () { setOpen(!open); });

  window.addEventListener("message", function (event) {
    // Both checks matter. Origin alone still lets another widget on the same
    // host talk to us; the source field alone lets any page talk to us.
    if (event.origin !== origin) return;
    const data = event.data;
    if (!data || data.source !== "powabase-widget") return;
    if (data.type === "close") setOpen(false);
  });

  if (read(KEY_OPEN) === "1") setOpen(true);
})();
```

- [ ] **Step 2: Syntax-check**

```bash
cd /Users/oscar/Downloads/rag-chatbot/frontend && node --check widget.js && node --check widget.css.js && echo "both parse"
```

- [ ] **Step 3: Confirm it declares nothing else on window**

```bash
cd /Users/oscar/Downloads/rag-chatbot && grep -c "^\(const\|let\|var\|function\) " frontend/widget.js | xargs echo "  top-level declarations outside the IIFE (expect 0):"
grep -n "window\." frontend/widget.js | sed 's/^/  /'
```

Expected: 0 top-level declarations; the only `window.` uses are the guard flag and `addEventListener`.

- [ ] **Step 4: Commit**

```bash
git add frontend/widget.js
git commit -m "feat: the embed widget loader"
```

---

### Task 4: Embed mode on the panel

**Files:**
- Modify: `frontend/share.js`
- Modify: `frontend/share.html`

**Interfaces:**
- Consumes: `GET /s/{token}/session/{id}/messages` (Task 1); the `#session=` fragment and `?embed=1` query written by the loader (Task 3).
- Produces: the two `postMessage` payloads the loader listens for.

**Context:** read `frontend/share.js` first. `TOKEN` comes from the path. `sessionId` starts null and the page creates one lazily on first send — in embed mode the loader has already made one, and the page must use it rather than creating a second. `appendMessage`-equivalent rendering lives in this file; find the function that renders a turn and reuse it for replay rather than writing a second renderer.

- [ ] **Step 1: Add the close control to the markup**

In `frontend/share.html`, inside `<header class="share-head">`, after the `<p id="bot-desc">`:

```html
        <button type="button" id="embed-close" class="icon-btn" aria-label="Close chat" hidden>×</button>
```

- [ ] **Step 2: Teach share.js about embed mode**

At the top of `frontend/share.js`, after the `TOKEN` line:

```javascript
// Embed mode: this page is inside the widget's iframe on somebody else's site.
// The loader owns the session and hands it over in the fragment, so the page
// resumes rather than starting a second conversation beside the first.
const EMBEDDED = new URLSearchParams(location.search).get("embed") === "1";
const HANDED_SESSION = new URLSearchParams(
  location.hash.replace(/^#/, "")
).get("session");
```

Replace `let sessionId = null;` with:

```javascript
let sessionId = HANDED_SESSION || null;
```

- [ ] **Step 3: Replay the transcript on boot**

Add this function above `boot()`:

```javascript
// Replay what the visitor already said, so navigating the host site does not
// throw their conversation away.
//
// A 404 here is not expected: the loader validates the id before handing it
// over, precisely so this page can trust it. Treat it as nothing to replay
// rather than as an error to show, and do NOT clear sessionId — clearing it
// would send the page down its own create-a-session path and put two owners
// on the same conversation.
async function replay() {
  if (!sessionId) return;
  const res = await fetch(
    `/s/${encodeURIComponent(TOKEN)}/session/${encodeURIComponent(sessionId)}/messages`
  );
  if (!res.ok) return;
  const body = await res.json();
  (body.messages || []).forEach((m) => {
    render(m.role, m.content, m.citations || [], m.answered_by || null);
  });
}
```

**Before writing this, find the existing render function in `share.js`** — the one `send()` uses to put a turn on screen — and call that, with its real name and argument order. Do not add a second renderer. If its signature differs from the call above, adapt the call, and say so in your report.

- [ ] **Step 4: Call it, and wire the close button**

At the end of `boot()`, after the info fetch succeeds:

```javascript
  if (EMBEDDED) {
    const close = document.getElementById("embed-close");
    close.hidden = false;
    close.addEventListener("click", () => {
      // The only two messages that cross the frame boundary are this and
      // "ready". No session id is ever posted out: the parent's origin cannot
      // be verified from in here, so nothing worth stealing is sent.
      parent.postMessage({ source: "powabase-widget", type: "close" }, "*");
    });
    parent.postMessage({ source: "powabase-widget", type: "ready" }, "*");
  }
  await replay();
```

- [ ] **Step 5: Syntax-check and verify by hand**

```bash
cd /Users/oscar/Downloads/rag-chatbot/frontend && for f in *.js; do node --check "$f" || echo "FAIL $f"; done && echo "all parse"
```

Then, with the backend running on 8000, open `http://localhost:8000/s/<token>?embed=1` directly and confirm the × appears; open `http://localhost:8000/s/<token>` and confirm it does not.

- [ ] **Step 6: Commit**

```bash
git add frontend/share.js frontend/share.html
git commit -m "feat: embed mode resumes a handed-over session"
```

---

### Task 5: Hand out the script tag

**Files:**
- Modify: `backend/app/api/routes/chatbots.py` (the `_share_response` helper, around line 100)
- Modify: `frontend/index.html` (the share modal)
- Modify: `frontend/dashboard.js` (`paintShare`)
- Test: `backend/tests/unit/test_routes_chatbots.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ShareResponse.widget` — the script tag string, alongside the existing `embed` iframe string.

**Context:** `_share_response` already builds `url` and `embed`. `ShareResponse` is a pydantic model in `app/models/schemas.py` and needs the new optional field. The dialog already has an embed textarea and a copy button; the widget snippet gets its own pair beside them, following that exact pattern.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_routes_chatbots.py`:

```python
def test_share_response_offers_the_widget_snippet_too():
    """Both snippets, because they are for different situations: the iframe for
    somebody who wants to place a rectangle themselves, the script tag for
    somebody who wants a tab on the edge of their page."""
    app, fakes = build_app()
    fakes.chatbots.rows.append({"id": "cb1", "owner_id": "o1", "name": "Bot"})
    fakes.share.token = "tok"

    body = TestClient(app).post("/chatbots/cb1/share").json()

    assert '<iframe' in body["embed"]
    assert '<script' in body["widget"]
    assert 'data-token="tok"' in body["widget"]
    assert "/widget.js" in body["widget"]


def test_an_unshared_chatbot_offers_neither_snippet():
    app, fakes = build_app()
    fakes.chatbots.rows.append({"id": "cb1", "owner_id": "o1", "name": "Bot"})

    body = TestClient(app).get("/chatbots/cb1/share").json()

    assert body["embed"] is None
    assert body["widget"] is None
```

Adapt `build_app()` / `fakes` names to whatever this test file already uses — read it first.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_routes_chatbots.py -q -k widget`

Expected: FAIL — `KeyError: 'widget'`.

- [ ] **Step 3: Add the field**

In `backend/app/models/schemas.py`, on `ShareResponse`, beside `embed`:

```python
    widget: Optional[str] = None
```

In `backend/app/api/routes/chatbots.py`, inside `_share_response`, after the `embed` assignment:

```python
    # The other way to embed: a tab on the edge of the page instead of a
    # rectangle in the middle of it. Same token, same public page underneath.
    widget = (
        f'<script src="{base}/widget.js" data-token="{token}" async></script>'
        if token else None
    )
```

and add `widget=widget,` to the `ShareResponse(...)` call.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_routes_chatbots.py -q`

- [ ] **Step 5: Show it in the dialog**

In `frontend/index.html`, in the share modal after the existing embed row:

```html
          <label class="share-field">Widget (a tab on the edge of the page)
            <textarea id="share-widget" rows="2" readonly></textarea>
            <button type="button" id="share-widget-copy">Copy</button>
          </label>
```

In `frontend/dashboard.js`, in `paintShare`, beside the existing embed lines:

```javascript
  const widget = (state && state.widget) || "";
  document.getElementById("share-widget").value = widget;
  document.getElementById("share-widget-copy").disabled = !widget;
```

and add `["share-widget-copy", "share-widget"]` to the array of copy-button pairs in `wireShare`.

Also blank it in `setShareFields`, next to the other fields — a stale snippet from the previously-opened chatbot appearing in the dialog is the bug that pattern exists to prevent.

- [ ] **Step 6: Verify the dialog still works**

```bash
cd /tmp/domtest && cp /Users/oscar/Downloads/rag-chatbot/frontend/*.js . && cp /Users/oscar/Downloads/rag-chatbot/tools/domtest/*.mjs . && node share_modal.mjs
```

Expected: passes. If it fails because it counts fields or buttons, update it to expect the new pair and say so.

- [ ] **Step 7: Commit**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
git add backend/ frontend/index.html frontend/dashboard.js
git commit -m "feat: offer the widget snippet in the share dialog"
```

---

### Task 6: DOM tests for the loader

**Files:**
- Create: `tools/domtest/widget.mjs`
- Modify: `tools/domtest/README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: a runner that exits non-zero on failure.

**Context:** read `tools/domtest/tour.mjs` for the `check()` / `flush()` helpers and the JSDOM options this project uses (`pretendToBeVisual: true` is required). This runner is different from the others in one way: it must build a **host page**, not this project's `index.html`. Construct a minimal document, inject the widget script the way a host site would, and drive it.

Serve the loader's fetches with a stub, and note the loader reads `document.currentScript` — jsdom sets that only while a classic script executes, so evaluate `widget.js` inside a `<script>` element appended to the document rather than with `w.eval`.

**Do NOT assert visibility with `getComputedStyle`.** Assert on the `data-open` attribute and on `frame.src`.

- [ ] **Step 1: Write the runner**

Cover exactly these:

1. **The shadow root is built** — `host.shadowRoot` exists, and the tab is inside it, not findable in the host document with `document.querySelector(".tab")`.
2. **Nothing leaks onto the host page** — no `<style>` added to the host document, and `Object.keys(window)` gains only `__powabaseWidget`.
3. **No session on page load** — after boot with the panel closed, the stub recorded zero requests to `/session`.
4. **First open creates one** — click the tab, exactly one `/session` request, and `frame.src` contains `#session=`.
5. **Second open reuses it** — close, open again, still exactly one `/session` request.
6. **The tab toggles** — `data-open` present after one click, absent after two, and `aria-expanded` tracks it.
7. **Open state persists** — with `powabase-widget:<token>:open` pre-set to `"1"`, the widget boots open.
8. **Session id persists** — with the session key pre-set and the stub answering its validation GET with 200, opening loads that id into the fragment and makes **no** `POST /session`.
8b. **A dead stored id is replaced, not handed over** — session key pre-set but the stub answers its validation GET with 404: the loader makes one `POST /session`, the fragment carries the NEW id, and the stored key is updated. *Without this the panel would be handed a session that does not exist and would quietly start creating its own, putting two owners on one conversation.*
9. **A close message from the right origin closes it.**
10. **A close message from the wrong origin is ignored** — same payload, different `event.origin`, panel stays open. *This is the check the whole message protocol exists for.*
11. **A message without the `source` field is ignored**, even from the right origin.
12. **Throwing storage still yields a working widget** — make `localStorage.getItem` throw before boot; the tab renders and toggles.
13. **Pasting the script twice builds one widget** — evaluate it twice, `document.querySelectorAll("[data-powabase-widget]").length === 1`.

- [ ] **Step 2: Run it**

```bash
cd /tmp/domtest && cp /Users/oscar/Downloads/rag-chatbot/frontend/*.js . && cp /Users/oscar/Downloads/rag-chatbot/tools/domtest/*.mjs . && node widget.mjs
```

Expected: all pass.

- [ ] **Step 3: Mutation-check the four that carry the design**

Make each edit in `frontend/widget.js`, re-copy, re-run, confirm the **named** check fails, then revert.

1. Move `ensureSession()` out of `load()` and call it at the end of the IIFE → check 3 must FAIL.
2. Delete `if (event.origin !== origin) return;` → check 10 must FAIL.
3. Delete `if (!data || data.source !== "powabase-widget") return;` → check 11 must FAIL.
4. Delete the `window.__powabaseWidget` guard → check 13 must FAIL.
5. Delete the validation branch in `ensureSession` (the `if (sessionId) { … }` block, leaving `if (sessionId) return sessionId;`) → check 8b must FAIL.

If any mutation leaves the suite green, say which branch the check actually reaches rather than adjusting it.

- [ ] **Step 4: Confirm every other runner still passes**

```bash
cd /tmp/domtest && for r in run scope share_modal onboarding tour_geometry tour_steps tour widget; do printf "%-16s " "$r"; node $r.mjs | tail -1; done
```

- [ ] **Step 5: Document and commit**

Add to `tools/domtest/README.md`:

```
node widget.mjs  # 14 checks: the embed widget loader, its shadow root, session ownership and message origin checks
```

```bash
git add tools/domtest/widget.mjs tools/domtest/README.md
git commit -m "test: DOM coverage for the embed widget loader"
```

---

### Task 7: Cross-origin verification by hand

**Files:**
- Modify: `/Users/oscar/Downloads/embed-test/index.html` (outside the repo — a real second origin, already served on port 4173)
- Create: `docs/widget-manual-checks.md`

**Interfaces:**
- Consumes: the assembled widget.
- Produces: a written record of what a person confirmed.

**Context:** jsdom has no layout engine and no real cross-origin model. It cannot tell you whether the tab is visible, whether the panel slides, or whether the host page's CSS wrecks it — and CSS collision is the specific failure this widget's shadow root exists to prevent, so a page with hostile CSS is the only real test of it.

- [ ] **Step 1: Give the test host hostile CSS**

Add to `/Users/oscar/Downloads/embed-test/index.html`, inside its `<style>`:

```css
/* Deliberately hostile, to prove the shadow boundary holds. A real host page
   will have rules like these by accident; the widget must survive them. */
* { box-sizing: content-box !important; }
div { margin: 12px !important; }
button { all: unset !important; font-size: 30px !important; }
iframe { border: 6px dashed red !important; }
```

- [ ] **Step 2: Swap the iframe for the widget**

Replace the `<iframe id="bot">` block with the script tag the Share dialog now produces, pointed at localhost:

```html
<script src="http://localhost:8000/widget.js" data-token="PASTE_YOUR_TOKEN" data-label="Ask us" async></script>
```

- [ ] **Step 3: Walk the checks and record real verdicts**

Create `docs/widget-manual-checks.md` with a row per check and a genuine pass/fail from looking:

| Check | Why no test can reach it |
|---|---|
| The tab is visible on the right edge and is not 30px tall | jsdom has no layout; this is the hostile-CSS check |
| Clicking slides the panel out rather than snapping | No layout, no transitions |
| The chat inside works — ask something, get an answer | Real cross-origin fetch |
| The × inside the panel closes it | Real postMessage across real origins |
| Reloading the host page reopens the panel with the thread intact | Real storage, real transcript endpoint |
| Navigating to another page on the host keeps the thread | The reason persistence exists |
| The iframe does not have a red dashed border | Proves host CSS did not reach inside the shadow root |
| At a 380px window the tab is a bottom pill and the panel is full width | Layout |
| Both themes are legible | Colour rendering |

- [ ] **Step 4: Record what is still unverified**

State plainly anything not confirmed rather than implying full coverage.

- [ ] **Step 5: Commit**

```bash
git add docs/widget-manual-checks.md
git commit -m "docs: manual verification record for the embed widget"
```
