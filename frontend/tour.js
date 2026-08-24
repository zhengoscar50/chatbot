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
let tourFormSeenOpen = false;  // latch for the description step; reset per step

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
  // Done when the FORM is done, not on the first keystroke — that fired on one
  // character and marched the user out of a half-filled form into a step
  // pointing at the sidebar behind it.
  //
  // "Modal is closed" alone is not enough either: arriving at this step with
  // the form already shut — a replay landing here, or the step-8 fallback
  // jumping back — would satisfy it instantly and skip the step. So it
  // latches: the form has to be seen open, and then closed.
  description: () => {
    const modal = document.getElementById("agent-modal");
    if (!modal.hidden) {
      tourFormSeenOpen = true;
      return false;
    }
    return tourFormSeenOpen;
  },
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

  tourTitle.textContent = copy.title;
  tourBody.textContent = copy.body;
  tourProgress.textContent = `${index + 1} of ${TOUR_STEPS.length}`;
  tourAction.hidden = true;
  tourFallbackShown = false;
  tourFormSeenOpen = false;
  tourDeadline = Date.now() + STEP_TIMEOUT_MS;
  tourBox.focus();
  tick();
}

function tick() {
  if (!tourRunning) return;
  const step = TOUR_STEPS[tourIndex];

  // Completion first, before anything about visibility. Doing the thing a step
  // asks for often destroys that step's own target — clicking a chatbot card
  // hides the whole dashboard — so a completion test gated on the target still
  // being on screen can never fire for exactly the steps that matter most.
  const satisfied = TOUR_DONE[step.id];
  if (satisfied && satisfied()) {
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
    const copy = stepCopy(step, stepMode(step, tourOnboarding));
    if (tourBody.textContent !== copy.body) tourBody.textContent = copy.body;
    // Next cannot cross to a surface the user has not reached — the target
    // does not exist there, and advancing would only trigger the rewind below
    // and look like a dead button. Reflect that instead of pretending.
    const next = TOUR_STEPS[tourIndex + 1];
    const blocked = !!next && next.surface !== currentSurface();
    tourNext.disabled = blocked;
    tourNext.title = blocked ? "Do this step to continue" : "";
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

  // 3. The target exists but its container is closed — the user walked past
  //    the step that opens it, which is their right: this is a walkthrough,
  //    not a setup wizard. Nobody has to create an agent to be shown what the
  //    description field is for. So keep the step's own explanation on screen,
  //    highlight nothing, and let Next carry them on. Saying "close this
  //    window" here would be advice for the opposite problem.
  if (target && target.closest("[hidden]")) {
    const copy = stepCopy(step, stepMode(step, tourOnboarding));
    if (tourBody.textContent !== copy.body) tourBody.textContent = copy.body;
    tourFrame = requestAnimationFrame(tick);
    return;
  }

  // 4. On screen but behind an open window. The user has to close it, and
  //    nothing else on the page will tell them so — waiting out the timeout
  //    in silence is exactly how a tour comes to feel broken.
  if (target && tourOccluded(target)) {
    tourBody.textContent = "Close this window to carry on with the tour.";
    tourFrame = requestAnimationFrame(tick);
    return;
  }

  // 5. Genuinely missing — usually a renamed selector. Offer a way out; a tour
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
  if (!tourFallbackShown) {
    tourFallbackShown = true;
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

async function startTour() {
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
