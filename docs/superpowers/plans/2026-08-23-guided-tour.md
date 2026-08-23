# Guided Tour Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An eight-step interactive tour that spotlights real controls, advances when the user actually performs each action, and replays from the `?` button.

**Architecture:** Three focused frontend files — pure spotlight geometry, a declarative step table, and a state machine that waits on conditions rather than listening for clicks. No backend change: `GET /onboarding` already derives which steps an account has completed, and each step reads its `done` flag to choose between "click this" and "this is where that lives".

**Tech Stack:** Vanilla JS, no build step, no module system. jsdom via `tools/domtest/` for behaviour tests.

**Spec:** `docs/superpowers/specs/2026-08-23-guided-tour-design.md`

## Global Constraints

- **No module system.** `frontend/*.js` are plain `<script src>` sharing one global scope, resolved by load order. No `import`/`export`, no bundler.
- **No backend change and no migration.** `GET /onboarding` already returns `{steps:[{id,label,hint,done}], complete}`.
- **The spotlight is four dimming panes**, never one overlay with a transparent hole. The highlighted control must be the only clickable thing on the page, achieved by geometry — never by `pointer-events` juggling or event interception.
- **Steps complete on state, not clicks.** A step's `done()` predicate is polled; it never listens for a click on its own target. This is what makes a cancelled prompt a non-event.
- **No step may hang.** Every wait has a timeout (`STEP_TIMEOUT_MS = 15000`) that surfaces a skip affordance.
- **Escape always ends the tour**, from any step. The tour must never be a keyboard trap.
- **Every `localStorage` read and write wrapped in try/catch.** Key: `rag-chat-tour-skipped`.
- **`textContent` only, never `innerHTML`, for any value.**
- **Never assert visibility with `getComputedStyle`** — jsdom special-cases the `hidden` attribute and reports `display:none` regardless of the cascade, so such an assertion cannot fail. Assert on `hidden` plus the static cascade audit in `run.mjs` Section L. See `tools/domtest/README.md`.
- **Run DOM tests:** `cd /tmp/domtest && cp <repo>/frontend/*.js . && cp <repo>/tools/domtest/*.mjs . && node <runner>.mjs`
- **Run backend tests:** `cd backend && .venv/bin/python -m pytest tests/ -q` (must stay at 556 passed — this plan changes no backend code).

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/tour-spotlight.js` (create) | Pure geometry: given a rect and a viewport, where do the four panes and the box go. No DOM queries, no state. |
| `frontend/tour-steps.js` (create) | The eight-step table: target selector, completion predicate, copy. Data plus one mode-selection function. |
| `frontend/tour.js` (create) | The engine: waiting, advancing, off-script recovery, start/skip/Escape. |
| `frontend/index.html` (modify) | Tour DOM (four panes, the box), three script tags. |
| `frontend/styles.css` (modify) | Pane and box styling, including `[hidden]` guards. |
| `frontend/onboarding.js` (modify) | `?` starts the tour instead of toggling the panel. |
| `frontend/app.js` (modify) | Set the one-shot autoplay flag after a successful register. |
| `tools/domtest/tour.mjs` (create) | Behaviour tests for the engine and wiring. |

Task 1 is pure arithmetic and fully testable in isolation. Task 2 is data. Task 3 depends on both. Tasks 4-5 are DOM and wiring. Task 6 tests the assembled whole.

---

### Task 1: Spotlight geometry

**Files:**
- Create: `frontend/tour-spotlight.js`
- Test: `tools/domtest/tour_geometry.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `panesFor(rect, viewport, pad)` → `{top, right, bottom, left}`, each `{x, y, width, height}`
  - `boxPlacement(rect, viewport, boxSize, gap)` → `{x, y, side}` where `side` is `"right" | "left" | "below" | "above" | "docked"`

**Context:** This file is pure functions over plain objects. It must not query the DOM, read globals, or hold state — that is what makes it testable without a layout engine.

- [ ] **Step 1: Write the failing tests**

Create `tools/domtest/tour_geometry.mjs`:

```javascript
// Pure geometry for the tour's spotlight. No DOM, no layout engine needed —
// which is the point: jsdom cannot lay anything out, so the arithmetic lives
// here where it can actually be tested.
import { readFileSync } from "fs";

const FE = "/Users/oscar/Downloads/rag-chatbot/frontend";
const results = [];
function check(ok, label, detail = "") {
  results.push(ok);
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
}

// Load the module into this scope the same way the browser would: no module
// system exists in this project, so eval the file and pull the globals out.
const src = readFileSync(`${FE}/tour-spotlight.js`, "utf8");
const scope = {};
new Function("exports", src + "\nexports.panesFor = panesFor; exports.boxPlacement = boxPlacement;")(scope);
const { panesFor, boxPlacement } = scope;

const VIEW = { width: 1000, height: 800 };

console.log("\n=== panes cover everything except the target ===");
{
  const rect = { x: 400, y: 300, width: 200, height: 100 };
  const p = panesFor(rect, VIEW, 0);

  check(p.top.y === 0 && p.top.height === 300, "top pane runs from the top edge to the target");
  check(p.bottom.y === 400 && p.bottom.height === 400, "bottom pane runs from the target to the bottom edge");
  check(p.left.x === 0 && p.left.width === 400, "left pane runs from the left edge to the target");
  check(p.right.x === 600 && p.right.width === 400, "right pane runs from the target to the right edge");

  // The whole point: the target rect is uncovered, so it is the only thing
  // on the page that can be clicked. If a pane overlapped it, the tour would
  // silently swallow the click it is asking the user to make.
  const overlaps = (a, b) =>
    a.x < b.x + b.width && a.x + a.width > b.x &&
    a.y < b.y + b.height && a.y + a.height > b.y;
  const offenders = Object.entries(p).filter(([, pane]) => overlaps(pane, rect));
  check(offenders.length === 0, "no pane overlaps the target rect",
        offenders.map(([n]) => n).join(","));
}

console.log("\n=== padding grows the hole, never the panes ===");
{
  const rect = { x: 400, y: 300, width: 200, height: 100 };
  const p = panesFor(rect, VIEW, 8);
  check(p.top.height === 292, "padding pulls the top pane back", String(p.top.height));
  check(p.left.width === 392, "padding pulls the left pane back", String(p.left.width));
}

console.log("\n=== a target at the very edge produces no negative panes ===");
{
  // A card flush against the left edge, or a button at the top of the page.
  // A negative width renders as a full-viewport pane in some browsers, which
  // would dim the target itself.
  const p = panesFor({ x: 0, y: 0, width: 100, height: 50 }, VIEW, 12);
  const negatives = Object.entries(p).filter(([, q]) => q.width < 0 || q.height < 0);
  check(negatives.length === 0, "no pane has a negative dimension",
        negatives.map(([n]) => n).join(","));
}

console.log("\n=== the box goes where there is room ===");
{
  const box = { width: 320, height: 160 };

  // Room on the right.
  let b = boxPlacement({ x: 100, y: 300, width: 120, height: 40 }, VIEW, box, 16);
  check(b.side === "right", "prefers the right of a left-hand target", b.side);

  // No room on the right — must flip rather than run off the viewport.
  b = boxPlacement({ x: 850, y: 300, width: 120, height: 40 }, VIEW, box, 16);
  check(b.side === "left", "flips to the left when the right would overflow", b.side);
  check(b.x >= 0, "flipped box stays on screen", String(b.x));

  // Neither side fits.
  b = boxPlacement({ x: 300, y: 40, width: 400, height: 40 }, VIEW, box, 16);
  check(b.side === "below", "drops below when neither side fits", b.side);
}

console.log("\n=== a narrow viewport docks the box ===");
{
  // On a phone there is no room beside anything. Docking is the documented
  // behaviour; overflowing is not.
  const b = boxPlacement({ x: 20, y: 300, width: 280, height: 40 },
                         { width: 360, height: 640 }, { width: 320, height: 160 }, 16);
  check(b.side === "docked", "docks on a narrow viewport", b.side);
}

console.log("\n=== the box never leaves the viewport, wherever the target is ===");
{
  // Property check rather than another example: sweep the target across the
  // whole viewport and assert the invariant holds every time.
  const box = { width: 320, height: 160 };
  let escaped = null;
  for (let x = 0; x <= 960 && !escaped; x += 40) {
    for (let y = 0; y <= 760 && !escaped; y += 40) {
      const b = boxPlacement({ x, y, width: 40, height: 40 }, VIEW, box, 16);
      if (b.x < 0 || b.y < 0 || b.x + box.width > VIEW.width || b.y + box.height > VIEW.height) {
        escaped = `target ${x},${y} -> box ${b.x},${b.y} (${b.side})`;
      }
    }
  }
  check(!escaped, "box stays on screen for every target position", escaped || "");
}

const failed = results.filter((r) => !r).length;
console.log(`\n${results.length} checks, ${results.length - failed} passed, ${failed} FAILED`);
process.exit(failed ? 1 : 0);
```

- [ ] **Step 2: Run it to verify it fails**

```bash
mkdir -p /tmp/domtest && cd /tmp/domtest
[ -d node_modules/jsdom ] || (npm init -y && npm install jsdom)
cp /Users/oscar/Downloads/rag-chatbot/tools/domtest/*.mjs .
node tour_geometry.mjs
```

Expected: fails at load — `ENOENT ... tour-spotlight.js`.

- [ ] **Step 3: Write the implementation**

Create `frontend/tour-spotlight.js`:

```javascript
// Where the tour's four dimming panes and its explanation box go.
//
// Four panes rather than one overlay with a transparent hole, because an
// overlay covers the target: clicks land on the overlay instead of the button
// the tour just asked you to press. Setting pointer-events:none fixes that and
// creates a worse problem — every click passes through, so the user wanders
// off-script mid-tour. Four panes make the geometry do the work: the target is
// genuinely uncovered and is the only clickable thing on the page.
//
// Pure functions over plain objects on purpose. jsdom has no layout engine, so
// arithmetic kept here is the only part of the spotlight that can be tested.

// Clamp so an edge-hugging target cannot produce a negative pane. A negative
// width renders as a full-viewport pane in some browsers, which would dim the
// very thing being highlighted.
function positive(n) {
  return n > 0 ? n : 0;
}

function panesFor(rect, viewport, pad) {
  const p = pad || 0;
  const top = positive(rect.y - p);
  const left = positive(rect.x - p);
  const right = rect.x + rect.width + p;
  const bottom = rect.y + rect.height + p;

  return {
    top: { x: 0, y: 0, width: viewport.width, height: top },
    bottom: { x: 0, y: bottom, width: viewport.width, height: positive(viewport.height - bottom) },
    left: { x: 0, y: top, width: left, height: positive(bottom - top) },
    right: { x: right, y: top, width: positive(viewport.width - right), height: positive(bottom - top) },
  };
}

function boxPlacement(rect, viewport, box, gap) {
  const g = gap || 0;

  // Nowhere beside anything fits on a phone. Dock rather than overflow.
  if (viewport.width < box.width + rect.width + g * 3) {
    return {
      x: Math.max(0, Math.round((viewport.width - box.width) / 2)),
      y: positive(viewport.height - box.height - g),
      side: "docked",
    };
  }

  const clampY = (y) => Math.min(positive(viewport.height - box.height), positive(y));
  const centredY = clampY(rect.y + rect.height / 2 - box.height / 2);

  const rightX = rect.x + rect.width + g;
  if (rightX + box.width <= viewport.width) {
    return { x: rightX, y: centredY, side: "right" };
  }

  const leftX = rect.x - g - box.width;
  if (leftX >= 0) {
    return { x: leftX, y: centredY, side: "left" };
  }

  const clampX = (x) => Math.min(positive(viewport.width - box.width), positive(x));
  const centredX = clampX(rect.x + rect.width / 2 - box.width / 2);

  const belowY = rect.y + rect.height + g;
  if (belowY + box.height <= viewport.height) {
    return { x: centredX, y: belowY, side: "below" };
  }
  return { x: centredX, y: clampY(rect.y - g - box.height), side: "above" };
}
```

- [ ] **Step 4: Run it to verify it passes**

```bash
cd /tmp/domtest && cp /Users/oscar/Downloads/rag-chatbot/frontend/*.js . && cp /Users/oscar/Downloads/rag-chatbot/tools/domtest/*.mjs . && node tour_geometry.mjs
```

Expected: 14 checks, 0 FAILED.

- [ ] **Step 5: Mutation-check the two load-bearing properties**

Make each edit, confirm the named check FAILS, then **revert**.

1. In `panesFor`, change `right: { x: right, ... }` to `right: { x: rect.x, ... }` — "no pane overlaps the target rect" must FAIL.
2. In `positive`, `return n;` instead of clamping — "no pane has a negative dimension" must FAIL.

- [ ] **Step 6: Commit**

```bash
git add frontend/tour-spotlight.js tools/domtest/tour_geometry.mjs
git commit -m "feat: spotlight geometry for the guided tour"
```

---

### Task 2: The step table

**Files:**
- Create: `frontend/tour-steps.js`
- Test: `tools/domtest/tour_steps.mjs`

**Interfaces:**
- Consumes: nothing (it only *declares* selectors and predicates; the engine runs them).
- Produces:
  - `TOUR_STEPS` — an array of eight `{id, surface, target, needs, title, doing, showing}` objects; the last also carries `fallbackTarget`, `fallbackTitle`, `fallbackBody`, `fallbackAction`
  - `stepMode(step, onboarding)` → `"doing" | "showing"`
  - `stepCopy(step, mode)` → `{title, body}`

**Context — the app's real DOM, verified:** `#dashboard-grid`, `.bot-card`, `#manage-agents`, `#agent-list-new`, `#agent-description`, `#my-knowledge`, `#chat-input` all exist. `.agent-badge` is rendered on an assistant message **only when a specialist answered** — it is absent for the general assistant, which is what makes step 8's check meaningful. Modals (`#agent-list-modal`, `#agent-modal`, `#knowledge-modal`) are in the static HTML and toggled via the `hidden` property.

Each step's `needs` maps it to one of the five ids `GET /onboarding` returns (`chatbot`, `agent`, `description`, `knowledge`, `answer`), or `null` for steps that are pure orientation.

- [ ] **Step 1: Write the failing tests**

Create `tools/domtest/tour_steps.mjs`:

```javascript
import { readFileSync } from "fs";

const FE = "/Users/oscar/Downloads/rag-chatbot/frontend";
const results = [];
function check(ok, label, detail = "") {
  results.push(ok);
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
}

const src = readFileSync(`${FE}/tour-steps.js`, "utf8");
const scope = {};
new Function("exports", src +
  "\nexports.TOUR_STEPS = TOUR_STEPS; exports.stepMode = stepMode; exports.stepCopy = stepCopy;")(scope);
const { TOUR_STEPS, stepMode, stepCopy } = scope;

const NOTHING_DONE = { steps: [
  { id: "chatbot", done: true }, { id: "agent", done: false },
  { id: "description", done: false }, { id: "knowledge", done: false },
  { id: "answer", done: false }] };
const ALL_DONE = { steps: NOTHING_DONE.steps.map((s) => ({ ...s, done: true })) };

console.log("\n=== the table itself ===");
{
  check(TOUR_STEPS.length === 8, "eight steps", String(TOUR_STEPS.length));
  check(new Set(TOUR_STEPS.map((s) => s.id)).size === 8, "step ids are unique");
  check(TOUR_STEPS.every((s) => typeof s.target === "string" && s.target),
        "every step names a target selector");
  check(TOUR_STEPS.every((s) => typeof s.needs === "string" || s.needs === null),
        "every step declares which onboarding id it needs, or null");
  check(TOUR_STEPS.every((s) => s.doing.trim() && s.showing.trim() && s.title.trim()),
        "every step carries copy for both modes");
}

console.log("\n=== the description step is the one this tour exists for ===");
{
  const d = TOUR_STEPS.find((s) => s.needs === "description");
  check(!!d, "a step maps to the description requirement");
  check(d.target === "#agent-description", "it targets the real field", d.target);
  // If this copy does not say why the field matters, the tour has not solved
  // the problem it was built for — it has only pointed at a text input.
  check(/rout/i.test(d.doing), "its copy explains that routing matches on it");
}

console.log("\n=== mode selection is what makes replay coherent ===");
{
  const agentStep = TOUR_STEPS.find((s) => s.needs === "agent");

  check(stepMode(agentStep, NOTHING_DONE) === "doing",
        "an unmet step tells the user to act");
  check(stepMode(agentStep, ALL_DONE) === "showing",
        "a step whose work is already done just points at it");

  // The failure this prevents: a fully set-up account presses ? and is ordered
  // to create a second copy of everything it already has.
  const modes = TOUR_STEPS.map((s) => stepMode(s, ALL_DONE));
  check(modes.every((m) => m === "showing"),
        "a complete account gets no instruction to create anything",
        modes.join(","));
}

console.log("\n=== orientation steps never demand work ===");
{
  const orientation = TOUR_STEPS.filter((s) => s.needs === null);
  check(orientation.length >= 1, "there is at least one orientation step");
  check(orientation.every((s) => stepMode(s, NOTHING_DONE) === "showing"),
        "orientation steps show even on an empty account");
}

console.log("\n=== missing or malformed onboarding data degrades to showing ===");
{
  // The tour must still run if /onboarding failed. Showing every step is the
  // safe degradation: it teaches, and it never orders an action whose
  // completion the engine cannot detect.
  const s = TOUR_STEPS.find((x) => x.needs === "agent");
  check(stepMode(s, null) === "showing", "null onboarding -> showing");
  check(stepMode(s, {}) === "showing", "no steps key -> showing");
  check(stepMode(s, { steps: [] }) === "showing", "empty steps -> showing");
}

console.log("\n=== copy selection ===");
{
  const s = TOUR_STEPS.find((x) => x.needs === "agent");
  check(stepCopy(s, "doing").body === s.doing, "doing mode uses the doing copy");
  check(stepCopy(s, "showing").body === s.showing, "showing mode uses the showing copy");
  check(stepCopy(s, "doing").title === s.title, "title is shared by both modes");
}

const failed = results.filter((r) => !r).length;
console.log(`\n${results.length} checks, ${results.length - failed} passed, ${failed} FAILED`);
process.exit(failed ? 1 : 0);
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd /tmp/domtest && cp /Users/oscar/Downloads/rag-chatbot/tools/domtest/*.mjs . && node tour_steps.mjs
```

Expected: fails at load — `ENOENT ... tour-steps.js`.

- [ ] **Step 3: Write the implementation**

Create `frontend/tour-steps.js`:

```javascript
// The eight steps of the guided tour.
//
// Declarative on purpose: each step names a target and a completion predicate,
// and the engine runs them. A step NEVER listens for a click on its own target
// — it polls whether the world changed. That is what makes a cancelled name
// prompt a non-event: nothing was created, the predicate is still false, and
// the step simply stands.
//
// `needs` maps a step to one of the five ids GET /onboarding derives, or null
// for pure orientation. That mapping is what makes replay coherent: a step
// whose work is already done points at the thing instead of demanding it.

const TOUR_STEPS = [
  {
    id: "grid",
    surface: "dashboard",
    target: "#dashboard-grid",
    needs: null,
    title: "Your chatbots",
    doing: "Each box is a chatbot. Its agents are listed inside it.",
    showing: "Each box is a chatbot. Its agents are listed inside it.",
  },
  {
    id: "enter",
    surface: "dashboard",
    target: ".bot-card:not(.bot-card--new)",
    needs: "chatbot",
    title: "Go inside",
    doing: "Click a chatbot to open it. This is where the work happens.",
    showing: "Clicking a chatbot opens it. This is where the work happens.",
  },
  {
    id: "agents",
    surface: "chat",
    target: "#manage-agents",
    needs: null,
    title: "Agents",
    doing: "Open Manage agents. Agents are the specialists your questions get routed to.",
    showing: "Manage agents is where your specialists live.",
  },
  {
    id: "new-agent",
    surface: "chat",
    target: "#agent-list-new",
    needs: "agent",
    title: "Add a specialist",
    doing: "Create an agent — one specialist for one subject works best.",
    showing: "This is where you add another specialist.",
  },
  {
    id: "description",
    surface: "chat",
    target: "#agent-description",
    needs: "description",
    title: "Describe it",
    doing: "Write what this agent is for. Routing matches your question against "
         + "this description, so an agent without one is never chosen.",
    showing: "Routing matches your question against this description. An agent "
           + "without one is never chosen.",
  },
  {
    id: "knowledge",
    surface: "chat",
    target: "#my-knowledge",
    needs: "knowledge",
    title: "Give it something to read",
    doing: "Upload a PDF. Anything here is readable by every agent in this chatbot.",
    showing: "Documents here are readable by every agent in this chatbot.",
  },
  {
    id: "ask",
    surface: "chat",
    target: "#chat-input",
    needs: "answer",
    title: "Ask it something",
    doing: "Ask a question your document covers, and watch which agent answers.",
    showing: "Ask a question your document covers, and watch which agent answers.",
  },
  {
    id: "who-answered",
    target: ".agent-badge",
    needs: null,
    surface: "chat",
    title: "A specialist answered",
    doing: "That badge names the agent that answered. That is routing and "
         + "retrieval both working.",
    showing: "That badge names the agent that answered. That is routing and "
           + "retrieval both working.",
    // If no badge exists, the general assistant answered — the exact failure
    // this tour was built to prevent, and the user is looking straight at it.
    // Falling back to the generic "can't find that" would waste the best
    // teaching moment the app ever gets.
    fallbackTarget: ".row--assistant:last-of-type",
    fallbackTitle: "The general assistant answered",
    fallbackBody: "No specialist was picked, so no document was searched. "
                + "That almost always means an agent's description does not "
                + "match the question.",
    fallbackAction: { label: "Check the description", stepId: "description" },
  },
];

function stepMode(step, onboarding) {
  // Orientation steps never demand work.
  if (!step.needs) return "showing";
  // A tour that cannot read progress still teaches. Showing everything is the
  // safe degradation — it never orders an action whose completion the engine
  // would then fail to detect.
  const steps = (onboarding && onboarding.steps) || null;
  if (!Array.isArray(steps) || steps.length === 0) return "showing";
  const match = steps.find((s) => s.id === step.needs);
  if (!match) return "showing";
  return match.done ? "showing" : "doing";
}

function stepCopy(step, mode) {
  return { title: step.title, body: mode === "doing" ? step.doing : step.showing };
}
```

- [ ] **Step 4: Run it to verify it passes**

```bash
cd /tmp/domtest && cp /Users/oscar/Downloads/rag-chatbot/frontend/*.js . && cp /Users/oscar/Downloads/rag-chatbot/tools/domtest/*.mjs . && node tour_steps.mjs
```

Expected: 18 checks, 0 FAILED.

- [ ] **Step 5: Mutation-check the two that carry the design**

Make each edit, confirm the named check FAILS, then **revert**.

1. In `stepMode`, `return "doing";` as the last line regardless of `match.done` — "a complete account gets no instruction to create anything" must FAIL.
2. In `stepMode`, delete the `if (!Array.isArray(steps) ...)` guard — the three "missing or malformed onboarding data" checks must FAIL (they will throw rather than return).

- [ ] **Step 6: Commit**

```bash
git add frontend/tour-steps.js tools/domtest/tour_steps.mjs
git commit -m "feat: the guided tour's eight-step table"
```

---

### Task 3: Tour DOM and styles

**Files:**
- Modify: `frontend/index.html` (tour DOM before the closing `</body>` scripts; three script tags)
- Modify: `frontend/styles.css` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: ids `tour`, `tour-pane-top`, `tour-pane-right`, `tour-pane-bottom`, `tour-pane-left`, `tour-box`, `tour-title`, `tour-body`, `tour-progress`, `tour-action`, `tour-next`, `tour-skip`. Task 4 wires all of them.

**Context:** colour tokens live on `:root` in `styles.css` and are redefined for dark mode, so styling through `var(--…)` gets dark mode for free. Never hardcode a hex value.

- [ ] **Step 1: Add the markup**

In `frontend/index.html`, immediately before the block of `<script src>` tags:

```html
    <div class="tour" id="tour" hidden>
      <div class="tour__pane" id="tour-pane-top" aria-hidden="true"></div>
      <div class="tour__pane" id="tour-pane-right" aria-hidden="true"></div>
      <div class="tour__pane" id="tour-pane-bottom" aria-hidden="true"></div>
      <div class="tour__pane" id="tour-pane-left" aria-hidden="true"></div>
      <div class="tour__box" id="tour-box" role="dialog" aria-live="polite"
           aria-labelledby="tour-title" tabindex="-1">
        <h2 class="tour__title" id="tour-title"></h2>
        <p class="tour__body" id="tour-body"></p>
        <div class="tour__foot">
          <span class="tour__progress" id="tour-progress"></span>
          <button type="button" class="tour__skip" id="tour-skip">Skip</button>
          <button type="button" class="tour__action" id="tour-action" hidden></button>
          <button type="button" class="tour__next" id="tour-next">Next</button>
        </div>
      </div>
    </div>
```

- [ ] **Step 2: Add the script tags**

In `frontend/index.html`, before `<script src="/dashboard.js"></script>`:

```html
    <script src="/tour-spotlight.js"></script>
    <script src="/tour-steps.js"></script>
    <script src="/tour.js"></script>
```

Order matters: `tour.js` calls into the other two at runtime, and all three must load before `app.js` runs `init()`.

- [ ] **Step 3: Add the styles**

Append to `frontend/styles.css`:

```css
/* Guided tour. Four panes dim the page and leave the target uncovered, so the
   highlighted control is the only clickable thing — geometry, not
   pointer-events juggling. */
.tour {
  position: fixed;
  inset: 0;
  z-index: 60;
  /* The container must not intercept anything: the panes below are the only
     part that blocks, and the gap between them IS the interactive target. */
  pointer-events: none;
}

/* The author rule above sets no display, but the guard is not optional here:
   the UA [hidden] rule loses to any author display, and this exact bug has
   shipped twice in this codebase (.app, then .bot-card__actions). */
.tour[hidden] {
  display: none;
}

.tour__pane {
  position: fixed;
  background: rgba(0, 0, 0, 0.55);
  pointer-events: auto;
  transition: all 120ms ease;
}

.tour__box {
  position: fixed;
  width: min(20rem, calc(100vw - 2rem));
  pointer-events: auto;
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--accent-border);
  border-radius: 12px;
  padding: 0.9rem 1rem;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.28);
}

.tour__title {
  margin: 0 0 0.35rem;
  font-size: 0.98rem;
  font-weight: 600;
}

.tour__body {
  margin: 0 0 0.75rem;
  font-size: 0.86rem;
  line-height: 1.5;
  color: var(--text-muted);
}

.tour__foot {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.tour__progress {
  flex: 1;
  font-size: 0.75rem;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.tour__skip {
  border: none;
  background: none;
  padding: 0.3rem 0.5rem;
  font-size: 0.8rem;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: 6px;
}

.tour__skip:hover {
  color: var(--text);
  background: var(--accent-hover);
}

.tour__next {
  border: none;
  border-radius: 8px;
  padding: 0.35rem 0.8rem;
  font-size: 0.82rem;
  background: var(--accent);
  color: var(--accent-contrast);
  cursor: pointer;
}

.tour__next:hover {
  filter: brightness(1.06);
}

/* Shown only when a step hits a state worth explaining — step 8 when the
   general assistant answered rather than a specialist. */
.tour__action {
  border: 1px solid var(--accent-border);
  border-radius: 8px;
  padding: 0.35rem 0.7rem;
  font-size: 0.8rem;
  background: var(--accent-surface);
  color: var(--text);
  cursor: pointer;
}

.tour__action[hidden] {
  display: none;
}

@media (prefers-reduced-motion: reduce) {
  .tour__pane {
    transition: none;
  }
}
```

- [ ] **Step 4: Verify ids are unique and present**

```bash
python3 - <<'EOF'
import re, collections
html = open("frontend/index.html").read()
ids = re.findall(r'id="([^"]+)"', html)
dupes = [i for i, n in collections.Counter(ids).items() if n > 1]
print("duplicate ids:", dupes or "none")
need = ["tour","tour-pane-top","tour-pane-right","tour-pane-bottom","tour-pane-left",
        "tour-box","tour-title","tour-body","tour-progress","tour-action","tour-next","tour-skip"]
missing = [n for n in need if f'id="{n}"' not in html]
print("missing:", missing or "none")
order = [html.index(f'src="/{f}"') for f in
         ["tour-spotlight.js","tour-steps.js","tour.js","dashboard.js","app.js"]]
print("script order correct:", order == sorted(order))
assert not dupes and not missing and order == sorted(order)
EOF
```

Expected: no duplicates, nothing missing, script order correct.

- [ ] **Step 5: Confirm the existing DOM suites still pass**

Adding a script tag has broken these before — `boot()` in each runner keeps its own script list.

```bash
cd /tmp/domtest && cp /Users/oscar/Downloads/rag-chatbot/frontend/*.js . && cp /Users/oscar/Downloads/rag-chatbot/tools/domtest/*.mjs . && for r in run scope share_modal onboarding; do echo "--- $r"; node $r.mjs | tail -1; done
```

If any runner fails because `boot()` does not load the three new files, **add them to that runner's script list** in the same position as `index.html`, and say so in your report. Expected once fixed: run 48, scope 14, share_modal 5, onboarding 24.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/styles.css tools/domtest/
git commit -m "feat: guided tour markup and styles"
```

---

### Task 4: The engine

**Files:**
- Create: `frontend/tour.js`
- Test: covered by Task 6 (`tools/domtest/tour.mjs`)

**Interfaces:**
- Consumes: `panesFor`, `boxPlacement` (Task 1); `TOUR_STEPS`, `stepMode`, `stepCopy` (Task 2); the ids from Task 3; `authFetch` from `app.js`.
- Produces: globals `startTour()`, `skipTour()`, `wireTour()`, `tourAutoplayIfFlagged()`, and `TOUR_SKIP_KEY`.

**Context:** no module system — plain globals. `#agent-list-modal`, `#agent-modal`, `#knowledge-modal` are static elements toggled via `.hidden`. `.agent-badge` renders on an assistant message **only when a specialist answered**.

- [ ] **Step 1: Write the implementation**

Create `frontend/tour.js`:

```javascript
// The guided tour's engine.
//
// A step declares a target and a completion predicate. The engine polls the
// predicate on an animation frame; it never listens for a click on the target.
// That is deliberate — testing state rather than clicks is what makes a
// cancelled prompt a non-event, and what lets a step be satisfied by the user
// doing the thing their own way instead of the way the tour expected.
//
// Nothing here hangs. Every wait has a timeout that surfaces a skip.

const TOUR_SKIP_KEY = "rag-chat-tour-skipped";
const TOUR_AUTOPLAY_FLAG = "rag-chat-tour-autoplay";
const STEP_TIMEOUT_MS = 15000;
const PANE_PAD = 6;
const BOX_GAP = 16;

const tourEl = document.getElementById("tour");
const tourBox = document.getElementById("tour-box");
const tourTitle = document.getElementById("tour-title");
const tourBody = document.getElementById("tour-body");
const tourProgress = document.getElementById("tour-progress");
const tourAction = document.getElementById("tour-action");
const tourPanes = {
  top: document.getElementById("tour-pane-top"),
  right: document.getElementById("tour-pane-right"),
  bottom: document.getElementById("tour-pane-bottom"),
  left: document.getElementById("tour-pane-left"),
};

let tourIndex = -1;
let tourOnboarding = null;
let tourRunning = false;
let tourFrame = null;
let tourDeadline = 0;

function tourStorage(fn, fallback) {
  try {
    return fn();
  } catch (err) {
    return fallback;
  }
}

// --- what each step is waiting for -----------------------------------------
// Keyed by step id. Each returns true once the world has changed in the way
// that step asked for. Kept here rather than in the step table so the table
// stays declarative data with no DOM knowledge in it.
const TOUR_DONE = {
  grid: () => true,
  enter: () => !document.getElementById("app-view").hidden,
  agents: () => !document.getElementById("agent-list-modal").hidden,
  "new-agent": () => !document.getElementById("agent-modal").hidden,
  description: () => {
    const el = document.getElementById("agent-description");
    return !!el && el.value.trim().length > 0;
  },
  knowledge: () => !document.getElementById("knowledge-modal").hidden,
  ask: () => document.querySelectorAll(".row--assistant").length > 0,
  "who-answered": () => true,
};

function tourVisible(el) {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

function paintPanes(rect) {
  const view = { width: window.innerWidth, height: window.innerHeight };
  const panes = panesFor(rect, view, PANE_PAD);
  Object.keys(tourPanes).forEach((k) => {
    const el = tourPanes[k];
    const p = panes[k];
    el.style.left = `${p.x}px`;
    el.style.top = `${p.y}px`;
    el.style.width = `${p.width}px`;
    el.style.height = `${p.height}px`;
  });
  const box = { width: tourBox.offsetWidth || 320, height: tourBox.offsetHeight || 160 };
  const place = boxPlacement(rect, view, box, BOX_GAP);
  tourBox.style.left = `${place.x}px`;
  tourBox.style.top = `${place.y}px`;
}

function showStep(index) {
  tourIndex = index;
  const step = TOUR_STEPS[index];
  const mode = stepMode(step, tourOnboarding);
  const copy = stepCopy(step, mode);

  tourTitle.textContent = copy.title;
  tourBody.textContent = copy.body;
  tourProgress.textContent = `${index + 1} of ${TOUR_STEPS.length}`;
  tourAction.hidden = true;
  tourDeadline = Date.now() + STEP_TIMEOUT_MS;
  tourBox.focus();
  tick();
}

function tick() {
  if (!tourRunning) return;
  const step = TOUR_STEPS[tourIndex];
  const target = document.querySelector(step.target);

  if (tourVisible(target)) {
    target.scrollIntoView({ block: "center", behavior: "auto" });
    paintPanes(target.getBoundingClientRect());
    const mode = stepMode(step, tourOnboarding);
    // Only a "doing" step advances on its own. A "showing" step is a caption
    // on something that already exists — the user reads it and presses Next.
    if (mode === "doing" && (TOUR_DONE[step.id] || (() => true))()) {
      advance();
      return;
    }
    tourFrame = requestAnimationFrame(tick);
    return;
  }

  // The target is not on screen. Three reasons, in order of how often they
  // happen, and none of them may result in silence.

  // 1. The user left the surface this step belongs to — walked back to the
  //    dashboard mid-step, most often. Rewind rather than wait forever for a
  //    control that cannot appear here.
  if (currentSurface() !== step.surface && step.surface !== "any") {
    const back = lastStepOnSurface(currentSurface());
    if (back !== -1 && back !== tourIndex) {
      showStep(back);
      return;
    }
  }

  // 2. This step has a fallback for a state that is not an error but is not
  //    what it hoped for. Step 8 uses it when no specialist answered.
  if (step.fallbackTarget && tourVisible(document.querySelector(step.fallbackTarget))) {
    showFallback(step);
    return;
  }

  // 3. Genuinely missing — usually a renamed selector. Offer a way out; a tour
  //    that quietly stalls is the thing that makes tours feel broken.
  if (Date.now() > tourDeadline) {
    tourBody.textContent = "Can't find that on screen — skip this step?";
  }
  tourFrame = requestAnimationFrame(tick);
}

function currentSurface() {
  return document.getElementById("app-view").hidden ? "dashboard" : "chat";
}

function lastStepOnSurface(surface) {
  for (let i = tourIndex; i >= 0; i -= 1) {
    if (TOUR_STEPS[i].surface === surface) return i;
  }
  return -1;
}

// A step's fallback state: real, expected, and worth explaining rather than
// treating as a missing target.
function showFallback(step) {
  const target = document.querySelector(step.fallbackTarget);
  paintPanes(target.getBoundingClientRect());
  tourTitle.textContent = step.fallbackTitle;
  tourBody.textContent = step.fallbackBody;
  if (step.fallbackAction) {
    tourAction.hidden = false;
    tourAction.textContent = step.fallbackAction.label;
    tourAction.onclick = () => {
      const i = TOUR_STEPS.findIndex((x) => x.id === step.fallbackAction.stepId);
      tourAction.hidden = true;
      // Send them back to fix it rather than yanking them there silently —
      // they asked for this by pressing the button.
      showStep(i === -1 ? 0 : i);
    };
  }
  tourFrame = requestAnimationFrame(tick);
}

function advance() {
  if (tourIndex + 1 >= TOUR_STEPS.length) {
    endTour();
    return;
  }
  showStep(tourIndex + 1);
}

function firstIncompleteStep() {
  // Replay starts where there is still work, so abandoning the tour costs
  // nothing and no step is ever demanded twice.
  const i = TOUR_STEPS.findIndex((s) => stepMode(s, tourOnboarding) === "doing");
  return i === -1 ? 0 : i;
}

async function startTour() {
  tourOnboarding = null;
  try {
    const res = await authFetch("/onboarding");
    if (res.ok) tourOnboarding = await res.json();
  } catch (err) {
    // The tour still teaches without progress data — every step shows.
    tourOnboarding = null;
  }
  tourRunning = true;
  tourEl.hidden = false;
  showStep(firstIncompleteStep());
}

function endTour() {
  tourRunning = false;
  tourEl.hidden = true;
  if (tourFrame) cancelAnimationFrame(tourFrame);
  tourFrame = null;
}

function skipTour() {
  tourStorage(() => localStorage.setItem(TOUR_SKIP_KEY, "1"), null);
  endTour();
}

// Autoplay is a one-shot handoff from a successful register, consumed by the
// dashboard's first render. A brand-new account is by definition a first visit,
// so this needs no durable storage of its own.
function tourAutoplayIfFlagged() {
  const flagged = tourStorage(() => sessionStorage.getItem(TOUR_AUTOPLAY_FLAG), null);
  if (flagged !== "1") return;
  tourStorage(() => sessionStorage.removeItem(TOUR_AUTOPLAY_FLAG), null);
  if (tourStorage(() => localStorage.getItem(TOUR_SKIP_KEY), null) === "1") return;
  startTour();
}

function wireTour() {
  document.getElementById("tour-next").addEventListener("click", advance);
  document.getElementById("tour-skip").addEventListener("click", skipTour);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && tourRunning) skipTour();
  });
  window.addEventListener("resize", () => {
    if (!tourRunning) return;
    const t = document.querySelector(TOUR_STEPS[tourIndex].target);
    if (tourVisible(t)) paintPanes(t.getBoundingClientRect());
  });
}
```

- [ ] **Step 2: Syntax-check every frontend file**

```bash
cd /Users/oscar/Downloads/rag-chatbot/frontend && for f in *.js; do node --check "$f" || echo "SYNTAX FAIL $f"; done && echo "all parse"
```

Expected: `all parse`

- [ ] **Step 3: Commit**

```bash
git add frontend/tour.js
git commit -m "feat: the guided tour engine"
```

---

### Task 5: Wiring — the ? button and autoplay on register

**Files:**
- Modify: `frontend/onboarding.js` (the `?` handler)
- Modify: `frontend/app.js` (set the autoplay flag after a successful register; call `wireTour()` and `tourAutoplayIfFlagged()`)
- Modify: `frontend/dashboard.js` (call `tourAutoplayIfFlagged()` after the grid renders)

**Interfaces:**
- Consumes: `startTour`, `wireTour`, `tourAutoplayIfFlagged`, `TOUR_AUTOPLAY_FLAG` (Task 4).
- Produces: no new names.

- [ ] **Step 1: Point `?` at the tour**

In `frontend/onboarding.js`, `wireOnboarding` currently binds `#onboarding-help` to `toggleOnboarding`. Change that one line so the button starts the tour:

```javascript
function wireOnboarding() {
  // The ? button now starts the guided tour. The checklist panel below still
  // auto-shows for someone mid-setup who does not want to be walked anywhere;
  // its close button remains the way to dismiss it.
  onboardHelp.addEventListener("click", startTour);
  document.getElementById("onboarding-close").addEventListener("click", hideOnboarding);
}
```

Leave `toggleOnboarding` in place — `hideOnboarding` and the auto-show path still use the surrounding machinery, and removing it would be a separate change.

- [ ] **Step 2: Flag autoplay after a successful register**

In `frontend/app.js`, at the point where a successful auth response is handled, the code already knows whether this was a register (`authMode === "register"`). Immediately after the token is stored, add:

```javascript
      // A brand-new account has never seen the app. Hand the tour a one-shot
      // flag rather than a durable one — first visit is implied by signup.
      if (authMode === "register") {
        try {
          sessionStorage.setItem("rag-chat-tour-autoplay", "1");
        } catch (err) {
          /* the tour simply does not autoplay; ? still starts it */
        }
      }
```

- [ ] **Step 3: Wire and consume**

In `frontend/app.js`, wherever `wireDashboard()` is called during `init()`, add alongside it:

```javascript
  wireTour();
```

In `frontend/dashboard.js`, at the end of `loadDashboard()`, immediately after the existing `refreshOnboarding();` line:

```javascript
  // After the grid exists — the tour's first steps target cards that
  // renderCard has only just created.
  tourAutoplayIfFlagged();
```

- [ ] **Step 4: Syntax-check**

```bash
cd /Users/oscar/Downloads/rag-chatbot/frontend && for f in *.js; do node --check "$f" || echo "SYNTAX FAIL $f"; done && echo "all parse"
```

- [ ] **Step 5: Commit**

```bash
git add frontend/onboarding.js frontend/app.js frontend/dashboard.js
git commit -m "feat: start the tour from ? and after signup"
```

---

### Task 6: Behaviour tests for the engine

**Files:**
- Create: `tools/domtest/tour.mjs`
- Modify: `tools/domtest/README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: a runner that exits non-zero on failure.

**Context:** read `tools/domtest/onboarding.mjs` first and copy its `boot()` helper and fake-server shape — it already stubs `/onboarding` with a controllable payload, which this runner needs too. jsdom implements `requestAnimationFrame`, so the engine's polling loop runs; use the existing `flush()` helper to let frames elapse.

**Do NOT assert visibility with `getComputedStyle`** — assert on `hidden`.

- [ ] **Step 1: Write the runner**

Cover exactly these, each as a labelled `check(...)`:

1. **`?` starts the tour** — click `#onboarding-help`, `#tour` is not hidden.
2. **A fully-complete account still gets a tour** — `/onboarding` reports all five done; `?` opens it and it shows step 1, not an empty tour.
3. **A complete account is never told to create anything** — with all five done, no rendered step body matches `/^Click |^Create |^Upload |^Open /`.
4. **An empty account starts at the first step with work.**
5. **A partially-complete account skips ahead** — with `chatbot` and `agent` done, the opening step is the description step, not step 1.
6. **Next advances** — progress reads "2 of 8" after one click.
7. **A doing-step does not advance on Next alone if its condition is unmet** — assert the step's own predicate is what gates it: with the description step showing in doing mode and the field empty, the engine does not auto-advance across several frames.
8. **A doing-step advances when its condition becomes true** — set `#agent-description`'s value, flush frames, the step advances without any click on the field.
9. **Skip hides the tour and writes the flag.**
10. **Escape ends the tour** from an arbitrary step.
11. **`?` replays despite the skip flag** — pre-set `rag-chat-tour-skipped`, click `?`, the tour opens.
12. **Autoplay does not fire when the skip flag is set.**
13. **Autoplay fires once** when the session flag is set, and **not again** on a second `loadDashboard()`.
14. **A failing `/onboarding` still runs the tour** — 500 response, tour opens, every step in showing mode, no console error.
15. **A missing target surfaces a skip rather than hanging** — point a step at a selector that matches nothing, advance time past the timeout, the body text changes to the can't-find message and the tour is still open.
16. **The four panes leave the target uncovered** — stub `getBoundingClientRect` on the target, read the four panes' inline styles, assert none overlaps the target rect.
17. **Step 8 explains a general-assistant answer instead of stalling** — render an assistant message with NO `.agent-badge`, drive the tour to the last step, and assert the box shows the fallback title (not "Can't find that"), that `#tour-action` is visible, and that clicking it lands on the description step. *This is the tour's most valuable moment; a generic timeout message there would waste it.*
18. **Step 8 shows the normal copy when a specialist DID answer** — with `.agent-badge` present, the fallback must not appear and `#tour-action` stays hidden. *Assert both halves or 17 proves nothing.*
19. **Leaving a surface rewinds rather than hanging** — drive to a chat-surface step, then show the dashboard (`#app-view` hidden), flush frames, and assert the tour moved back to a dashboard step rather than sitting on an unreachable target.
20. **`#tour-action` is cleared when a new step is shown** — after the fallback appears, advance, and assert the button is hidden again.

- [ ] **Step 2: Run it**

```bash
cd /tmp/domtest && cp /Users/oscar/Downloads/rag-chatbot/frontend/*.js . && cp /Users/oscar/Downloads/rag-chatbot/tools/domtest/*.mjs . && node tour.mjs
```

Expected: all checks `[ok  ]`, exit 0.

- [ ] **Step 3: Mutation-check the four that carry the feature**

Make each edit, re-copy, re-run, confirm the NAMED check fails, then **revert**.

1. In `tour-steps.js` `stepMode`, always `return "doing"` — check 3 must FAIL.
2. In `tour.js` `tick`, drop the `mode === "doing" &&` guard so any step auto-advances — check 7 must FAIL.
3. In `tour.js` `tourAutoplayIfFlagged`, remove the `removeItem` call — check 13's "not again" half must FAIL.
4. In `tour.js` `startTour`, remove `firstIncompleteStep()` and always start at 0 — check 5 must FAIL.
5. In `tour.js` `tick`, delete the `step.fallbackTarget` branch — check 17 must FAIL.
6. In `tour.js` `tick`, delete the `currentSurface() !== step.surface` branch — check 19 must FAIL.

If any mutation does not produce the named failure, that check is not testing what it claims — fix the check and say so.

- [ ] **Step 4: Confirm every other runner still passes**

```bash
cd /tmp/domtest && for r in run scope share_modal onboarding tour_geometry tour_steps tour; do printf "%-16s " "$r"; node $r.mjs | tail -1; done
```

Expected: run 48, scope 14, share_modal 5, onboarding 24, plus the three tour runners green.

- [ ] **Step 5: Document the runners**

In `tools/domtest/README.md`, add:

```
node tour_geometry.mjs  # 14 checks: spotlight pane and box arithmetic
node tour_steps.mjs     # 18 checks: the step table and doing/showing mode selection
node tour.mjs           # 20 checks: the engine, autoplay, skip, replay, timeouts
```

- [ ] **Step 6: Commit**

```bash
git add tools/domtest/tour.mjs tools/domtest/README.md
git commit -m "test: behaviour coverage for the guided tour engine"
```

---

### Task 7: Manual verification pass

**Files:**
- Create: `docs/tour-manual-checks.md`

**Interfaces:**
- Consumes: the assembled feature.
- Produces: a written record of what a person confirmed and what is still unknown.

**Context:** jsdom has no layout engine. Every user-visible bug in this app so far — the `.app` cascade bug, the permanently-open card menus — was a layout or cascade bug that every automated check passed. The spotlight is *entirely* a layout feature, so this pass is not optional paperwork; it is the only thing that can verify the core of it.

- [ ] **Step 1: Start the app**

```bash
cd /Users/oscar/Downloads/rag-chatbot/backend && set -a && . ./.env && set +a && \
  .venv/bin/python -m uvicorn app.main:app --port 8000
```

- [ ] **Step 2: Walk the checks and record each verdict**

Create `docs/tour-manual-checks.md` with a row per check and a real pass/fail from actually looking:

| Check | Why it cannot be automated |
|---|---|
| The dim panes align to the highlighted control with no seam or gap | No layout engine |
| The highlighted control is genuinely clickable; clicks near it are blocked | Requires real hit-testing |
| The box does not cover the control it describes, on any step | Layout |
| Both themes: the box is legible against the dimmed page | Colour rendering |
| A narrow window (≤400px) docks the box and does not overflow | Layout |
| Scrolling mid-step keeps the panes on the target | Real scroll |
| Tab from the box reaches the highlighted control | Real focus order |
| Escape ends the tour from every surface | Cheap to confirm by hand |
| The whole eight-step arc, as a new account, end to end | The actual product |

- [ ] **Step 3: Record what is still unverified**

State plainly anything not confirmed, rather than implying full coverage.

- [ ] **Step 4: Commit**

```bash
git add docs/tour-manual-checks.md
git commit -m "docs: manual verification record for the guided tour"
```
