// The dashboard's getting-started checklist.
//
// Two jobs in one panel. While steps remain it shows itself and reads as "what
// to do next". Pressing Help reopens it at any time — including long after
// everything is ticked — and it reads as "what each part does". Same five
// steps, same server-owned copy; `helpMode` decides how much of the hint text
// renders.
//
// Visibility is ONE boolean. The render path never re-checks `complete` or the
// dismissal flag: two opinions about when a panel is on screen is how a Help
// button ends up refusing to open.

const ONBOARD_DISMISS_KEY = "rag-chat-onboarding-dismissed";

const onboardPanel = document.getElementById("onboarding");
const onboardNote = document.getElementById("onboarding-note");
const onboardSteps = document.getElementById("onboarding-steps");
const onboardHelp = document.getElementById("onboarding-help");

let onboardState = null;   // last payload from GET /onboarding
let helpOpen = false;      // the single source of truth for visibility
let helpMode = false;      // opened deliberately, so show every hint

// Storage throws outright in some contexts (site data blocked, private mode),
// and a dashboard that fails to paint because of a dismissal flag would be a
// far worse bug than the panel showing once too often.
function onboardDismissed() {
  try {
    return localStorage.getItem(ONBOARD_DISMISS_KEY) === "1";
  } catch (err) {
    return false;
  }
}

function rememberOnboardDismissed() {
  try {
    localStorage.setItem(ONBOARD_DISMISS_KEY, "1");
  } catch (err) {
    /* nothing to do — the panel simply shows again next visit */
  }
}

function renderOnboarding() {
  onboardPanel.hidden = !helpOpen;
  onboardHelp.setAttribute("aria-expanded", helpOpen ? "true" : "false");
  if (!helpOpen || !onboardState) return;

  const steps = onboardState.steps || [];
  // A wall of ticks needs a line saying what it is now for, or it reads as a
  // checklist with nothing left in it.
  const allDone = onboardState.complete;
  onboardNote.hidden = !(helpMode && allDone);
  onboardNote.textContent = "All set. Here is what each part does.";

  onboardSteps.innerHTML = "";
  steps.forEach((step) => {
    const li = document.createElement("li");
    li.className = "onboard__step" + (step.done ? " onboard__step--done" : "");
    li.dataset.step = step.id;

    const tick = document.createElement("span");
    tick.className = "onboard__tick";
    tick.textContent = step.done ? "✓" : "○";
    tick.setAttribute("aria-hidden", "true");
    li.appendChild(tick);

    const label = document.createElement("span");
    label.className = "onboard__label";
    // textContent, never innerHTML: this copy is the server's, but the habit is
    // what keeps the next person from interpolating a chatbot name in here.
    label.textContent = step.done ? `${step.label} — done` : step.label;
    li.appendChild(label);

    // The hint is the reason the step matters. Noise while you are working down
    // the list; the whole point when you came back to read.
    if (helpMode || !step.done) {
      const hint = document.createElement("span");
      hint.className = "onboard__hint";
      hint.textContent = step.hint;
      li.appendChild(hint);
    }

    onboardSteps.appendChild(li);
  });
}

function hideOnboarding() {
  // Only worth remembering while there is still something to come back to.
  // Once complete the panel never opens itself, so there is nothing to suppress.
  if (onboardState && !onboardState.complete) rememberOnboardDismissed();
  helpOpen = false;
  helpMode = false;
  renderOnboarding();
}

function toggleOnboarding() {
  if (helpOpen) {
    hideOnboarding();
    return;
  }
  helpOpen = true;
  helpMode = true;
  renderOnboarding();
}

// Called on every dashboard load. Decides whether the panel shows itself, and
// leaves an already-open panel open so a refresh mid-read does not shut it.
async function refreshOnboarding() {
  try {
    const res = await authFetch("/onboarding");
    if (!res.ok) return;
    onboardState = await res.json();
  } catch (err) {
    // A checklist is not worth breaking the dashboard over.
    return;
  }
  if (!helpOpen) {
    helpOpen = !onboardState.complete && !onboardDismissed();
    helpMode = false;
  }
  renderOnboarding();
}

function wireOnboarding() {
  onboardHelp.addEventListener("click", toggleOnboarding);
  document.getElementById("onboarding-close").addEventListener("click", hideOnboarding);
}
