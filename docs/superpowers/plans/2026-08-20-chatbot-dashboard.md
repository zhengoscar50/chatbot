# Chatbot Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sidebar's chatbot `<select>` with a dashboard of cards, one per chatbot, each listing its agents.

**Architecture:** A third top-level section in `index.html`, switched with `element.hidden` exactly as the login gate already is. A new `dashboard.js` owns the grid and the two functions every screen transition goes through; `chatbots.js` is reduced to chatbot state. No router, no build step, no backend change.

**Tech Stack:** Vanilla ES5-style browser JS loaded with `<script src>` tags — no modules, no bundler, no framework. Globals are shared across files by load order. Plain CSS with custom properties.

## Global Constraints

- **No backend file may change.** `cd backend && .venv/bin/python -m pytest -q` must stay at **455 passed** — any movement means something unintended was edited.
- `node --test frontend/*.test.js` must stay at **16 passing**. Do not modify `markdown.test.js`.
- **Never build DOM from `innerHTML` with server-supplied values.** Chatbot names, descriptions and agent names are user-controlled. Use `createElement` and `textContent`, as every other file in this codebase does. `innerHTML = ""` to clear a container is fine and is the existing idiom.
- **No new dependency, no build step, no `import`/`export`.** Files share globals via `<script>` order.
- Colours come from the existing custom properties (`--bg`, `--bg-subtle`, `--text`, `--text-muted`, `--border`, `--accent`, `--accent-surface`, `--accent-border`, `--accent-hover`). Never hard-code a hex value — the dark theme is driven entirely by these.
- These modules have **no automated test coverage** and this plan adds none. Verification is the manual click-through in Task 5.
- Test commands: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` and `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js`. Bare `python` is not on the PATH.

---

## File Structure

**Created**
- `frontend/dashboard.js` — the grid: screen switching, card rendering, card actions.

**Modified**
- `frontend/index.html` — new `#dashboard` section, `id="app-view"` on `.app`, a `← Dashboard` topbar button, a `<script>` tag, and (Task 4) removal of three sidebar controls.
- `frontend/app.js` — `enterApp()` routes to dashboard or chatbot; `doLogout()` clears the session key.
- `frontend/chatbots.js` — reduced to state: the list and `currentChatbotId`. Loses the picker, `CHATBOT_KEY`, and its create/delete handlers.
- `frontend/styles.css` — grid and card rules.

**Ownership split:** `chatbots.js` owns chatbot *data*; `dashboard.js` owns the *view*. This mirrors the existing split between `agents.js` (state and forms) and `scope.js` (one focused control).

---

### Task 1: Screen switching

**Files:**
- Create: `frontend/dashboard.js`
- Modify: `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`

**Interfaces:**
- Consumes: `authGate`, `currentUserLabel`, `newSessionButton`, `setComposerEnabled`, `clearThread`, `activeTitle`, `currentSessionId`, `loadAgents`, `loadSessions` (all globals in `app.js`/`agents.js`); `chatbots`, `currentChatbotId`, `loadChatbots` (`chatbots.js`).
- Produces: `showDashboard()`, `enterChatbot(id)`, `wireDashboard()`, and the constant `CHATBOT_SESSION_KEY = "rag-chat-inside"`. Later tasks call `loadDashboard()`, defined here as a stub and filled in by Task 2.

**After this task the app cannot be used, and that is expected.** The dashboard
renders an empty grid, and since the sidebar lives inside the now-hidden chat
view, there is no way to reach a chatbot until Task 2 draws the cards. Do not
"fix" this by leaving the old picker reachable or by auto-entering the first
chatbot — both would have to be undone. Commit the task as specified and move
on.

- [ ] **Step 1: Add the dashboard section and the Dashboard button to `index.html`**

Give the existing chat container an id. Change:

```html
    <div class="app">
```

to:

```html
    <div class="app" id="app-view" hidden>
```

Immediately **before** that line, add the new section:

```html
    <div class="dashboard" id="dashboard" hidden>
      <header class="dashboard__head">
        <h1 class="dashboard__title">Your chatbots</h1>
        <button type="button" id="dashboard-logout" class="logout-btn">Log out</button>
      </header>
      <p class="dashboard__status" id="dashboard-status"></p>
      <div class="dashboard__grid" id="dashboard-grid"></div>
    </div>
```

In the topbar, add the return button as the first child of `<header class="topbar">`, before `#sidebar-toggle`:

```html
          <button type="button" id="to-dashboard" class="scope-button">← Dashboard</button>
```

Add the script tag **after `chatbots.js` and before `app.js`** — `app.js` calls into it at boot, and load order is the only dependency mechanism here:

```html
    <script src="/chatbots.js"></script>
    <script src="/dashboard.js"></script>
    <script src="/app.js"></script>
```

- [ ] **Step 2: Create `frontend/dashboard.js`**

```js
// The dashboard: a card per chatbot, each listing its agents.
//
// Every screen transition goes through showDashboard() or enterChatbot() —
// never by setting `hidden` at a call site. Three sections toggled by a boolean
// is simple until one path forgets a toggle and two screens render at once, so
// there is exactly one place for that bug to live.

// Which chatbot you are currently INSIDE. sessionStorage, not localStorage, on
// purpose: a refresh keeps your place, a new tab starts at the dashboard.
const CHATBOT_SESSION_KEY = "rag-chat-inside";

const dashboard = document.getElementById("dashboard");
const dashboardGrid = document.getElementById("dashboard-grid");
const dashboardStatus = document.getElementById("dashboard-status");
const appView = document.getElementById("app-view");

function wireDashboard() {
  document.getElementById("to-dashboard").addEventListener("click", showDashboard);
  document.getElementById("dashboard-logout").addEventListener("click", doLogout);
}

async function showDashboard() {
  // Leaving a chatbot clears the resume marker, so the next refresh stays here
  // rather than bouncing back into the chatbot you deliberately left.
  sessionStorage.removeItem(CHATBOT_SESSION_KEY);
  currentChatbotId = null;
  currentSessionId = null;
  appView.hidden = true;
  dashboard.hidden = false;
  await loadDashboard();
}

async function enterChatbot(id) {
  currentChatbotId = id;
  sessionStorage.setItem(CHATBOT_SESSION_KEY, id);
  dashboard.hidden = true;
  appView.hidden = false;
  const bot = chatbots.find((c) => c.id === id);
  activeTitle.textContent = (bot && bot.name) || "RAG Chat";
  currentSessionId = null;
  clearThread("Pick or create a chat to start.");
  // Agents and chats both belong to a chatbot, so currentChatbotId must
  // already be set before either request goes out.
  await loadAgents();
  await loadSessions();
}

// Filled in by the next task; declared here so the transitions above work.
async function loadDashboard() {
  dashboardGrid.innerHTML = "";
}
```

- [ ] **Step 3: Route at boot in `app.js`**

Replace the body of `enterApp()` with:

```js
async function enterApp() {
  authGate.hidden = true;
  currentUserLabel.textContent = currentUsername || "";
  newSessionButton.disabled = false;
  setComposerEnabled(true);
  await loadChatbots();
  // The dashboard is where you START, not somewhere you are sent back to. A
  // refresh mid-conversation resumes; a new tab does not.
  const resume = sessionStorage.getItem(CHATBOT_SESSION_KEY);
  if (resume && chatbots.some((c) => c.id === resume)) {
    await enterChatbot(resume);
  } else {
    await showDashboard();
  }
}
```

In `showAuthGate()`, hide both screens so logging out cannot leave one visible:

```js
function showAuthGate() {
  authGate.hidden = false;
  dashboard.hidden = true;
  appView.hidden = true;
  setComposerEnabled(false);
}
```

In `doLogout()`, add this line beside the other two `removeItem` calls:

```js
  sessionStorage.removeItem(CHATBOT_SESSION_KEY);
```

Finally, call `wireDashboard();` immediately after the existing `wireChatbots();` call.

- [ ] **Step 4: Make `hidden` actually hide the chat view**

**Without this the whole task silently does nothing.** `styles.css:59` sets
`.app { display: flex }`, and a class selector outranks the browser's built-in
`[hidden] { display: none }` — so `appView.hidden = true` would leave the chat
view on screen and both screens would render at once. The codebase already hit
this once and solved it at `.auth-gate[hidden]` (line 486).

Append to `styles.css`:

```css
/* A class selector with `display` beats the UA's [hidden] rule, so `hidden`
   must be restated for any element that has one. Same reason .auth-gate[hidden]
   exists above. */
.app[hidden],
.dashboard[hidden] {
  display: none;
}
```

Verify before moving on:

```bash
cd /Users/oscar/Downloads/rag-chatbot/frontend && grep -n "app\[hidden\]" styles.css
```

Expected: one match.

- [ ] **Step 5: Check both suites are untouched**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q`
Expected: `455 passed`.

Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js`
Expected: 16 pass.

Run: `node --check frontend/dashboard.js && node --check frontend/app.js`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add frontend/dashboard.js frontend/index.html frontend/app.js frontend/styles.css
git commit -m "feat: dashboard screen switching"
```

---

### Task 2: Render the cards

**Files:**
- Modify: `frontend/dashboard.js`, `frontend/styles.css`

**Interfaces:**
- Consumes: `showDashboard()`, `enterChatbot(id)`, `loadDashboard()` from Task 1; `authFetch`, `errorText` from `app.js`; `chatbots`, `loadChatbots` from `chatbots.js`.
- Produces: `loadDashboard()` fully implemented, plus `renderCard(detail)` where `detail` is `{bot, agents, chats}` — `bot` is a chatbot row, `agents` an array of agent rows, `chats` a number.

- [ ] **Step 1: Replace the `loadDashboard` stub in `dashboard.js`**

```js
const CARD_AGENT_LIMIT = 3;

async function loadDashboard() {
  dashboardStatus.textContent = "Loading…";
  dashboardGrid.innerHTML = "";
  if (!(await loadChatbots())) {
    dashboardStatus.textContent = "Could not load your chatbots.";
    return;
  }
  // Each card needs its own agents and chats. Fanned out rather than awaited
  // in sequence, so one slow chatbot does not hold up the rest of the grid.
  const details = await Promise.all(chatbots.map(loadCardDetail));
  dashboardStatus.textContent = "";
  dashboardGrid.innerHTML = "";
  details.forEach((detail) => dashboardGrid.appendChild(renderCard(detail)));
}

async function loadCardDetail(bot) {
  const q = "?chatbot_id=" + encodeURIComponent(bot.id);
  const get = async (path) => {
    try {
      const res = await authFetch(path + q);
      return res.ok ? await res.json() : [];
    } catch (err) {
      return [];   // a card with no counts beats a grid that fails to draw
    }
  };
  const [agentRows, sessionRows] = await Promise.all([get("/agents"), get("/sessions")]);
  return { bot, agents: agentRows, chats: sessionRows.length };
}

function renderCard({ bot, agents: roster, chats }) {
  const card = document.createElement("article");
  card.className = "bot-card";
  card.dataset.id = bot.id;
  card.tabIndex = 0;
  card.setAttribute("role", "button");

  const head = document.createElement("header");
  head.className = "bot-card__head";
  const name = document.createElement("h2");
  name.className = "bot-card__name";
  name.textContent = bot.name;
  name.title = bot.name;          // the CSS truncates; the tooltip does not
  head.appendChild(name);
  card.appendChild(head);

  if (bot.description) {
    const desc = document.createElement("p");
    desc.className = "bot-card__desc";
    desc.textContent = bot.description;
    card.appendChild(desc);
  }

  const label = document.createElement("p");
  label.className = "bot-card__label";
  label.textContent = "Agents";
  card.appendChild(label);

  if (roster.length === 0) {
    // An empty region reads as a failed load, so say it plainly instead.
    const none = document.createElement("p");
    none.className = "bot-card__none";
    none.textContent = "No agents yet";
    card.appendChild(none);
  } else {
    const list = document.createElement("ul");
    list.className = "bot-card__agents";
    roster.slice(0, CARD_AGENT_LIMIT).forEach((a) => {
      const li = document.createElement("li");
      li.textContent = a.name;
      list.appendChild(li);
    });
    card.appendChild(list);
    if (roster.length > CARD_AGENT_LIMIT) {
      const more = document.createElement("p");
      more.className = "bot-card__more";
      more.textContent = `+${roster.length - CARD_AGENT_LIMIT} more`;
      card.appendChild(more);
    }
  }

  const count = document.createElement("p");
  count.className = "bot-card__count";
  count.textContent = chats === 1 ? "1 chat" : `${chats} chats`;
  card.appendChild(count);

  card.addEventListener("click", () => enterChatbot(bot.id));
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      enterChatbot(bot.id);
    }
  });
  return card;
}
```

- [ ] **Step 2: Make `loadChatbots` report success in `chatbots.js`**

It currently returns `undefined` on both paths. `loadDashboard` needs to tell them apart:

```js
async function loadChatbots() {
  const res = await authFetch("/chatbots");
  if (!res.ok) return false;
  chatbots = await res.json();
  const remembered = localStorage.getItem(CHATBOT_KEY);
  const exists = chatbots.some((c) => c.id === remembered);
  currentChatbotId = exists ? remembered : (chatbots[0] && chatbots[0].id) || null;
  renderChatbotSelect();
  return true;
}
```

Leave the rest of the function alone — Task 4 removes the picker lines. Adding
only the return values keeps this task reviewable on its own.

One consequence to expect and leave alone: this still assigns `currentChatbotId`
from `localStorage`, so it becomes non-null again right after `showDashboard()`
sets it to null. Nothing reads it while the dashboard is showing, and Task 4
deletes those lines. Do not remove them early — the picker still needs them
until then.

- [ ] **Step 3: Add the grid and card styles to `styles.css`**

Append. Every colour comes from an existing custom property, so dark mode
follows with no extra rules:

```css
.dashboard {
  padding: 32px 24px 48px;
  max-width: 1100px;
  margin: 0 auto;
}
.dashboard__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 4px;
}
.dashboard__title { font-size: 22px; margin: 0; }
.dashboard__status { color: var(--text-muted); min-height: 20px; margin: 4px 0 16px; }
.dashboard__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}
.bot-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-subtle);
  cursor: pointer;
  text-align: left;
}
.bot-card:hover, .bot-card:focus-visible {
  border-color: var(--accent-border);
  background: var(--accent-surface);
  outline: none;
}
.bot-card__head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.bot-card__name {
  font-size: 16px; margin: 0; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.bot-card__desc {
  color: var(--text-muted); font-size: 13px; margin: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.bot-card__label {
  color: var(--text-muted); font-size: 11px; letter-spacing: .08em;
  text-transform: uppercase; margin: 8px 0 0;
}
.bot-card__agents { list-style: none; margin: 0; padding: 0; font-size: 14px; }
.bot-card__agents li {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.bot-card__none, .bot-card__more { color: var(--text-muted); font-size: 13px; margin: 0; }
.bot-card__count {
  color: var(--text-muted); font-size: 13px; margin: 10px 0 0;
  border-top: 1px solid var(--border); padding-top: 10px;
}
```

- [ ] **Step 4: Check both suites and syntax**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — expected `455 passed`.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.
Run: `node --check frontend/dashboard.js && node --check frontend/chatbots.js`

- [ ] **Step 5: Commit**

```bash
git add frontend/dashboard.js frontend/chatbots.js frontend/styles.css
git commit -m "feat: render chatbot cards on the dashboard"
```

---

### Task 3: Card actions — new, rename, delete

**Files:**
- Modify: `frontend/dashboard.js`, `frontend/styles.css`

**Interfaces:**
- Consumes: `renderCard(detail)` and `loadDashboard()` from Task 2.
- Produces: a `⋯` menu on each card, a `+ New chatbot` tile, and the three handlers `createChatbotFromDashboard()`, `renameChatbot(bot)`, `deleteChatbotFromDashboard(bot)`.

`prompt()` and `confirm()` are what `chatbots.js` already uses for these three
actions. Keep them — a bespoke modal is a bigger change than this task, and
consistency beats novelty here.

- [ ] **Step 1: Add the menu to the card in `dashboard.js`**

Inside `renderCard`, after `head.appendChild(name);`, add:

```js
  const menuButton = document.createElement("button");
  menuButton.type = "button";
  menuButton.className = "bot-card__menu";
  menuButton.textContent = "⋯";
  menuButton.setAttribute("aria-label", `Actions for ${bot.name}`);
  head.appendChild(menuButton);

  const actions = document.createElement("div");
  actions.className = "bot-card__actions";
  actions.hidden = true;
  [["Rename", () => renameChatbot(bot)],
   ["Delete", () => deleteChatbotFromDashboard(bot)]].forEach(([text, run]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.addEventListener("click", (event) => {
      event.stopPropagation();   // the card behind opens the chatbot
      actions.hidden = true;
      run();
    });
    actions.appendChild(b);
  });
  card.appendChild(actions);

  menuButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const wasHidden = actions.hidden;
    closeAllCardMenus();
    actions.hidden = !wasHidden;
  });
```

Add at module level:

```js
function closeAllCardMenus() {
  dashboardGrid.querySelectorAll(".bot-card__actions").forEach((el) => {
    el.hidden = true;
  });
}
```

and in `wireDashboard()`, close menus when clicking elsewhere:

```js
  document.addEventListener("click", closeAllCardMenus);
```

`stopPropagation` on the menu button keeps this listener from immediately
reclosing the menu it just opened.

- [ ] **Step 2: Add the New tile and the three handlers**

At the end of `loadDashboard()`, after the `details.forEach(...)` line:

```js
  dashboardGrid.appendChild(renderNewTile());
```

Then add:

```js
function renderNewTile() {
  const tile = document.createElement("button");
  tile.type = "button";
  tile.className = "bot-card bot-card--new";
  tile.textContent = "+ New chatbot";
  tile.addEventListener("click", createChatbotFromDashboard);
  return tile;
}

async function createChatbotFromDashboard() {
  const name = prompt("Name this chatbot");
  if (!name) return;
  const res = await authFetch("/chatbots", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    dashboardStatus.textContent = await detailOf(res, "Could not create that chatbot.");
    return;
  }
  await loadDashboard();
}

async function renameChatbot(bot) {
  const name = prompt("Rename this chatbot", bot.name);
  if (!name || name === bot.name) return;
  const res = await authFetch(`/chatbots/${encodeURIComponent(bot.id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    dashboardStatus.textContent = await detailOf(res, "Could not rename that chatbot.");
    return;
  }
  await loadDashboard();
}

async function deleteChatbotFromDashboard(bot) {
  // Counted only now, not on every card: the document count is the one number
  // that costs a Powabase round trip per knowledge tier.
  let docs = 0;
  try {
    const res = await authFetch(
      `/knowledge/documents?chatbot_id=${encodeURIComponent(bot.id)}`
    );
    if (res.ok) docs = (await res.json()).length;
  } catch (err) {
    docs = 0;
  }
  const roster = await authFetch(
    `/agents?chatbot_id=${encodeURIComponent(bot.id)}`
  ).then((r) => (r.ok ? r.json() : [])).catch(() => []);
  const chats = await authFetch(
    `/sessions?chatbot_id=${encodeURIComponent(bot.id)}`
  ).then((r) => (r.ok ? r.json() : [])).catch(() => []);

  const message =
    `Delete "${bot.name}"?\n\n` +
    `This removes ${roster.length} agents, ${chats.length} chats, ` +
    `and ${docs} documents.\n\nThis can't be undone.`;
  if (!confirm(message)) return;

  const res = await authFetch(`/chatbots/${encodeURIComponent(bot.id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    // The server refuses to delete a user's last chatbot with 400 and a
    // message. Show its message rather than inventing one.
    dashboardStatus.textContent = await detailOf(res, "Could not delete that chatbot.");
    return;
  }
  await loadDashboard();
}

async function detailOf(res, fallback) {
  try {
    const body = await res.json();
    return errorText(body, res) || fallback;
  } catch (err) {
    return fallback;
  }
}
```

- [ ] **Step 3: Style the menu and the tile**

Append to `styles.css`:

```css
.bot-card { position: relative; }
.bot-card__menu {
  border: none; background: none; cursor: pointer; font-size: 18px;
  line-height: 1; padding: 0 4px; color: var(--text-muted); border-radius: 6px;
}
.bot-card__menu:hover { background: var(--accent-hover); color: var(--text); }
.bot-card__actions {
  position: absolute; top: 40px; right: 12px; z-index: 2;
  display: flex; flex-direction: column; min-width: 120px;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg); overflow: hidden;
}
.bot-card__actions button {
  border: none; background: none; cursor: pointer; text-align: left;
  padding: 8px 12px; font-size: 14px; color: var(--text);
}
.bot-card__actions button:hover { background: var(--accent-hover); }
.bot-card--new {
  align-items: center; justify-content: center; min-height: 140px;
  color: var(--text-muted); font-size: 15px;
  border-style: dashed; background: none;
}
```

- [ ] **Step 4: Check both suites and syntax**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — expected `455 passed`.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.
Run: `node --check frontend/dashboard.js`

- [ ] **Step 5: Commit**

```bash
git add frontend/dashboard.js frontend/styles.css
git commit -m "feat: create, rename and delete chatbots from the dashboard"
```

---

### Task 4: Retire the sidebar picker

**Files:**
- Modify: `frontend/index.html`, `frontend/chatbots.js`

**Interfaces:**
- Consumes: `loadDashboard()`, `enterChatbot(id)` — the dashboard now owns everything the picker did.
- Produces: `chatbots.js` reduced to `chatbots`, `currentChatbotId`, `loadChatbots() -> boolean`. `wireChatbots()`, `renderChatbotSelect()`, `createChatbot()`, `deleteChatbot()` and `CHATBOT_KEY` are all gone.

Do this only after Task 3, or the app is left with no way to create a chatbot.

- [ ] **Step 1: Remove the three sidebar controls from `index.html`**

Delete these four lines from the sidebar:

```html
        <label class="chatbot-picker">
          <select id="chatbot-select"></select>
        </label>
        <button type="button" id="new-chatbot" class="new-session">+ New chatbot</button>
        <button type="button" id="delete-chatbot" class="new-session">Delete chatbot</button>
```

Leave `⚙ Manage agents`, `📚 Chatbot knowledge`, `+ New chat`, the session list,
the status line and the admin link exactly as they are.

- [ ] **Step 2: Reduce `chatbots.js` to state**

Replace the whole file with:

```js
// A chatbot groups agents and the chats that use them. This file owns the
// chatbot LIST and which one is open; the dashboard owns how they are shown.
//
// There is deliberately no persistence here. Which chatbot you are inside is
// session-scoped and lives in dashboard.js — a localStorage key existed only
// to preselect the old sidebar picker, and outlived it.

let chatbots = [];
let currentChatbotId = null;

async function loadChatbots() {
  try {
    const res = await authFetch("/chatbots");
    if (!res.ok) return false;
    chatbots = await res.json();
    return true;
  } catch (err) {
    return false;
  }
}
```

- [ ] **Step 3: Drop the `wireChatbots()` call from `app.js`**

The function no longer exists. Remove the line `wireChatbots();` — `wireDashboard();`, added in Task 1, stands in its place.

- [ ] **Step 4: Confirm nothing still references the removed names**

Run:

```bash
cd /Users/oscar/Downloads/rag-chatbot/frontend && \
  grep -n "chatbot-select\|new-chatbot\|delete-chatbot\|CHATBOT_KEY\|renderChatbotSelect\|wireChatbots\|createChatbot\b\|deleteChatbot\b" *.js *.html
```

Expected: **no output.** Any hit is a dangling reference that will throw at
load and break every script that follows it.

- [ ] **Step 5: Check both suites and syntax**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — expected `455 passed`.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.
Run: `node --check frontend/chatbots.js && node --check frontend/app.js`

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/chatbots.js frontend/app.js
git commit -m "feat: retire the sidebar chatbot picker"
```

---

### Task 5: Failure states and the click-through

**Files:**
- Modify: `frontend/dashboard.js`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: a retry button on a failed dashboard load, and a graceful path when a chatbot disappears underneath you.

The dashboard is now the only way to switch chatbots. If it fails to draw, the
user is stranded — so it must always offer a way forward.

- [ ] **Step 1: Add the retry state**

Replace the failure branch inside `loadDashboard()`:

```js
  if (!(await loadChatbots())) {
    showDashboardError("Could not load your chatbots.");
    return;
  }
```

and add:

```js
function showDashboardError(message) {
  dashboardStatus.textContent = message;
  dashboardGrid.innerHTML = "";
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "bot-card bot-card--new";
  retry.textContent = "Try again";
  retry.addEventListener("click", loadDashboard);
  dashboardGrid.appendChild(retry);
}
```

- [ ] **Step 2: Handle a chatbot that vanished**

A chatbot deleted in another tab still has a card until the grid reloads.
Entering it would set `currentChatbotId` to a dead id and leave the user in a
chat view where every request 404s. Replace `enterChatbot`:

```js
async function enterChatbot(id) {
  const bot = chatbots.find((c) => c.id === id);
  if (!bot) {
    // Deleted in another tab, or a stale resume marker. Redraw rather than
    // entering a chatbot that is not there.
    dashboardStatus.textContent = "That chatbot no longer exists.";
    await loadDashboard();
    return;
  }
  currentChatbotId = id;
  sessionStorage.setItem(CHATBOT_SESSION_KEY, id);
  dashboard.hidden = true;
  appView.hidden = false;
  activeTitle.textContent = bot.name;
  currentSessionId = null;
  clearThread("Pick or create a chat to start.");
  await loadAgents();
  await loadSessions();
}
```

`enterApp()` already checks `chatbots.some(...)` before resuming, so a stale
`sessionStorage` id lands on the dashboard rather than here — this guard covers
the other-tab case and any future caller.

- [ ] **Step 3: Check both suites and syntax**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — expected `455 passed`.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.
Run: `node --check frontend/dashboard.js`

- [ ] **Step 4: Static cross-check of every id and global**

These modules have no automated coverage, so this replaces it. Run:

```bash
cd /Users/oscar/Downloads/rag-chatbot/frontend && \
  grep -ho 'getElementById("[a-z-]*")' *.js | sed 's/.*("\(.*\)")/\1/' | sort -u > /tmp/js_ids.txt && \
  grep -ho 'id="[a-z-]*"' *.html | sed 's/id="\(.*\)"/\1/' | sort -u > /tmp/html_ids.txt && \
  comm -23 /tmp/js_ids.txt /tmp/html_ids.txt
```

Expected: **no output.** Any line is an id referenced in JS that no longer
exists in the HTML — which throws at load and silently kills every script after
it in the load order.

- [ ] **Step 5: The click-through (this is the real verification)**

Start the backend, open the app, and walk these in order. Every step must pass:

1. Log in → the dashboard appears, one card per chatbot.
2. Each card lists its own agents; a chatbot with more than three shows `+N more`.
3. A chatbot with no agents shows "No agents yet", not a blank gap.
4. Click a card → that chatbot opens, its chats in the sidebar, its name in the topbar.
5. Refresh → you stay in that chatbot.
6. `← Dashboard` → the grid; refresh again → still the grid.
7. Open a new tab → the dashboard, not the chatbot.
8. `+ New chatbot` tile → a new empty card appears.
9. `⋯ → Rename` → the card name updates.
10. `⋯ → Delete` → the confirmation names agents, chats **and documents**.
11. Delete your only chatbot → the server's message appears, nothing is deleted.
12. Ask a question inside a chatbot → the answer and citations are unchanged.
13. Log out, log back in → the dashboard, no stale chatbot.
14. Toggle dark mode on the dashboard → cards follow the theme.

- [ ] **Step 6: Commit**

```bash
git add frontend/dashboard.js
git commit -m "feat: dashboard failure states"
```

---

## Deploy

Frontend-only and static — no migration, no backend restart needed for
correctness, but the box serves these files from the repo:

```bash
ssh -i ~/Downloads/key.pem ubuntu@3.21.125.6 \
  'cd ~/rag-chatbot && git pull && sudo systemctl restart ragchat'
```

Do not restart `cloudflared` — the public hostname is regenerated when it
restarts. Hard-refresh the browser afterwards; these are cached JS files.
