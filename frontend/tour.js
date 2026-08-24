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

// The last step has nothing after it, so "carry on with the tour" is a promise
// the tour cannot keep. Everything the user reads at the end should say so.
function isLastStep() {
  return tourIndex === TOUR_STEPS.length - 1;
}
const PANE_PAD = 6;
const BOX_GAP = 16;

const tourEl = document.getElementById("tour");
const tourBox = document.getElementById("tour-box");
const tourTitle = document.getElementById("tour-title");
const tourBody = document.getElementById("tour-body");
const tourProgress = document.getElementById("tour-progress");
const tourAction = document.getElementById("tour-action");
const tourNext = document.getElementById("tour-next");
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
let tourFallbackShown = false;
let tourPanelSeenOpen = false;  // latch for `opens` steps; reset per step

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
// A step listed here advances itself the moment the user does the thing. A step
// ABSENT from this map is orientation — a caption on something already on
// screen — and waits for Next. Presence, not the copy mode, is what decides:
// following the instructions has to carry you forward whether the step was
// phrased as "click this" or "this is where that lives".
const TOUR_DONE = {
  enter: () => !document.getElementById("app-view").hidden,
  agents: () => !document.getElementById("agent-list-modal").hidden,
  "new-agent": () => !document.getElementById("agent-modal").hidden,
  // Clicking the highlighted control is what advances a step. "description" is
  // the exception and is absent here: its instruction is to WRITE something,
  // not to click something, so it declares `opens` and finishes when the form
  // closes (see panelCycled). Everything else moves the moment the user does
  // the thing the step pointed at — waiting longer reads as the tour ignoring
  // them.
  knowledge: () => !document.getElementById("knowledge-modal").hidden,
  ask: () => document.querySelectorAll(".row--assistant").length > 0,
};

// Whether an open modal is covering the page with this element outside it.
// The tour paints above modals (z-index 60 vs 20), so an occluded target still
// gets a hole cut around it — a hole the user cannot click through, because the
// modal is what receives the click. Highlighting an unreachable control is
// worse than waiting for it to become reachable.
function tourOccluded(el) {
  const open = document.querySelectorAll(".modal:not([hidden])");
  if (!open.length) return false;
  for (const m of open) {
    if (m.contains(el)) return false;
  }
  return true;
}

// A step that declares `opens` is working inside a panel. It completes when
// that panel has been seen open and is then closed — not when it opens.
//
// Both halves matter. Completing on OPEN meant "Upload a PDF" was satisfied by
// merely looking at the panel, and "Write what this agent is for" by the first
// keystroke. Completing on CLOSED alone would satisfy the step instantly for
// anyone arriving with the panel already shut — a replay, or the step-8
// fallback jumping back — skipping the very step they were sent to.
function panelCycled(selector) {
  const el = document.querySelector(selector);
  if (!el) return false;
  if (!el.hidden) {
    tourPanelSeenOpen = true;
    return false;
  }
  return tourPanelSeenOpen;
}

function tourVisible(el) {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  if (!(r.width > 0 && r.height > 0)) return false;
  return !tourOccluded(el);
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

// Collapse the panes to nothing. Whenever the tour is not highlighting a live
// target it must not be blocking the page either — the panes are what make
// everything except the hole unclickable, so leaving them at the last step's
// geometry locks the user out of the very control the box is telling them to
// press. This is the difference between "waiting" and "frozen".
// Title and body always move together. They used to be set independently, so a
// waiting state replaced the body and left the previous step's title above it —
// "Ask it something" sitting over "Close this window to carry on", which reads
// as two steps at once. Writes are skipped when unchanged: this runs on every
// animation frame.
function setBox(title, body) {
  if (tourTitle.textContent !== title) tourTitle.textContent = title;
  if (tourBody.textContent !== body) tourBody.textContent = body;
}

function stepBox(step) {
  const copy = stepCopy(step, stepMode(step, tourOnboarding));
  setBox(copy.title, copy.body);
}

function clearPanes() {
  Object.keys(tourPanes).forEach((k) => {
    const el = tourPanes[k];
    el.style.width = "0px";
    el.style.height = "0px";
  });
}

function showStep(index) {
  // tick() is about to be called synchronously below. Any frame already
  // queued would then run a SECOND loop against the new step — and two loops
  // in one frame can both see a doing-predicate flip and both advance,
  // skipping a step the user never saw. Every path into a new step goes
  // through here, so one cancel covers all of them.
  if (tourFrame) cancelAnimationFrame(tourFrame);
  tourFrame = null;
  tourIndex = index;
  const step = TOUR_STEPS[index];
  const mode = stepMode(step, tourOnboarding);
  const copy = stepCopy(step, mode);

  setBox(copy.title, copy.body);
  tourProgress.textContent = `${index + 1} of ${TOUR_STEPS.length}`;
  tourNext.textContent = isLastStep() ? "Done" : "Next";
  tourAction.hidden = true;
  tourFallbackShown = false;
  tourPanelSeenOpen = false;
  tourDeadline = Date.now() + STEP_TIMEOUT_MS;
  tourBox.focus();
  tick();
}

function tick() {
  if (!tourRunning) return;
  const step = TOUR_STEPS[tourIndex];

  // Refresh this every frame, not only on the path where the target is
  // visible: a waiting step used to keep whatever state the button had last,
  // so it could sit there looking clickable while advance() silently refused.
  const upcoming = TOUR_STEPS[tourIndex + 1];
  tourNext.disabled = !!upcoming && upcoming.surface !== currentSurface();
  tourNext.title = tourNext.disabled ? "Do this step to continue" : "";

  // Completion first, before anything about visibility. Doing the thing a step
  // asks for often destroys that step's own target — clicking a chatbot card
  // hides the whole dashboard — so a completion test gated on the target still
  // being on screen can never fire for exactly the steps that matter most.
  const predicate = TOUR_DONE[step.id];
  const done = step.opens ? panelCycled(step.opens) : !!(predicate && predicate());
  if (done) {
    advance();
    return;
  }

  const target = document.querySelector(step.target);

  if (tourVisible(target)) {
    target.scrollIntoView({ block: "center", behavior: "auto" });
    paintPanes(target.getBoundingClientRect());
    // The waiting messages below overwrite the body. Once the target is
    // reachable again the step's own copy has to come back, or the box keeps
    // telling the user to close a window they already closed.
    stepBox(step);
    tourFrame = requestAnimationFrame(tick);
    return;
  }

  // The target is not on screen. Three reasons, in order of how often they
  // happen, and none of them may result in silence.

  // 1. The user left the surface this step belongs to — walked back to the
  //    dashboard mid-step, most often. Rewind rather than wait forever for a
  //    control that cannot appear here.
  if (currentSurface() !== step.surface && step.surface !== "any") {
    const back = stepOnSurface(currentSurface());
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

  // 3. The target exists but its container is closed — the user walked past
  //    the step that opens it, which is their right: this is a walkthrough,
  //    not a setup wizard. Nobody has to create an agent to be shown what the
  //    description field is for. So keep the step's own explanation on screen,
  //    highlight nothing, and let Next carry them on. Saying "close this
  //    window" here would be advice for the opposite problem.
  if (target && target.closest("[hidden]")) {
    clearPanes();
    stepBox(step);
    tourFrame = requestAnimationFrame(tick);
    return;
  }

  // 4. On screen but behind an open window. The user has to close it, and
  //    nothing else on the page will tell them so — waiting out the timeout
  //    in silence is exactly how a tour comes to feel broken.
  if (target && tourOccluded(target)) {
    // Whatever is covering the target, the user has to reach it — either to
    // work in it or to close it. Stop blocking the page.
    clearPanes();
    // If the thing covering the target is the panel THIS step asked them to
    // open, they are doing the step, not blocked by something unrelated.
    // Keep the instructions up rather than telling them to close it.
    const own = step.opens && document.querySelector(step.opens);
    if (own && !own.hidden) {
      stepBox(step);
      tourFrame = requestAnimationFrame(tick);
      return;
    }
    setBox("When you're done here", isLastStep()
      ? "Close this window to finish the tour."
      : "Close this window to carry on with the tour.");
    tourFrame = requestAnimationFrame(tick);
    return;
  }

  // 5. Genuinely missing — usually a renamed selector. Offer a way out; a tour
  //    that quietly stalls is the thing that makes tours feel broken.
  clearPanes();
  // A step can declare what it means for its subject not to exist yet. That is
  // a normal state to walk through, not a missing selector to apologise for.
  if (!target && step.emptyBody) {
    setBox(step.emptyTitle, step.emptyBody);
    tourFrame = requestAnimationFrame(tick);
    return;
  }
  if (Date.now() > tourDeadline) {
    setBox("Can't find that", "It may have moved. Skip this step?");
  }
  tourFrame = requestAnimationFrame(tick);
}

function currentSurface() {
  return document.getElementById("app-view").hidden ? "dashboard" : "chat";
}

// The step to move to when the user is somewhere the current step is not.
// Backward first — that is the common case, someone walking back to the
// dashboard mid-step. Then forward, because the user can also run AHEAD of the
// tour: clicking a chatbot while step 1 is still describing the grid used to
// strand the tour on a dashboard step forever, with Next unable to cross onto
// the surface the user had already reached. Catching up is always better than
// locking.
function stepOnSurface(surface) {
  for (let i = tourIndex; i >= 0; i -= 1) {
    if (TOUR_STEPS[i].surface === surface) return i;
  }
  for (let i = tourIndex + 1; i < TOUR_STEPS.length; i += 1) {
    if (TOUR_STEPS[i].surface === surface) return i;
  }
  return -1;
}

// A step's fallback state: real, expected, and worth explaining rather than
// treating as a missing target.
function showFallback(step) {
  const target = document.querySelector(step.fallbackTarget);
  paintPanes(target.getBoundingClientRect());
  if (!tourFallbackShown) {
    tourFallbackShown = true;
    setBox(step.fallbackTitle, step.fallbackBody);
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
  }
  tourFrame = requestAnimationFrame(tick);
}

function advance() {
  const from = tourIndex;
  // Belt and braces over the rewind in tick(), which would return the user
  // here anyway — so removing this changes no outcome a test can observe, and
  // none is written for it. It earns its place by making the round trip not
  // happen at all, and by covering the one case the rewind cannot: a step with
  // no earlier same-surface step to fall back to.
  const next = TOUR_STEPS[tourIndex + 1];
  if (next && next.surface !== currentSurface()) return;
  if (tourIndex + 1 >= TOUR_STEPS.length) {
    endTour();
    return;
  }
  // Belt and braces against the leak above: if two callers race, only the one
  // that still sees the step it was called for gets to move.
  if (from !== tourIndex) return;
  showStep(tourIndex + 1);
}

function firstIncompleteStep() {
  // Replay starts where there is still work, so abandoning the tour costs
  // nothing and no step is ever demanded twice.
  const i = TOUR_STEPS.findIndex((s) => stepMode(s, tourOnboarding) === "doing");
  return i === -1 ? 0 : i;
}

// `fromStart` separates the two entry points. Autoplay after signup shows a
// person who has seen nothing the whole tour from step 1 — skipping the
// dashboard orientation for the newest possible user is exactly backwards.
// Pressing ? later is a replay, and resumes at the first step with work left
// so nobody is made to sit through what they have already done.
async function startTour(fromStart) {
  // The ? button calls this; pressing it mid-tour must restart cleanly rather
  // than layering a second loop over the running one.
  if (tourRunning) endTour();
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
  showStep(fromStart ? 0 : firstIncompleteStep());
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
  startTour(true);   // a brand-new account starts at the beginning
}

function wireTour() {
  tourNext.addEventListener("click", advance);
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
