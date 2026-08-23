// The dashboard's getting-started checklist.
//
// A passive progress view: while steps remain it shows itself and reads as
// "what to do next," dismissible with its own close button. It no longer has
// a Help-driven mode — the ? button launches the guided tour instead (see
// tour.js). Same five steps, same server-owned copy.
//
// Visibility is ONE boolean. The render path never re-checks `complete` or the
// dismissal flag: two opinions about when a panel is on screen is how a Help
// button ends up refusing to open.

const ONBOARD_DISMISS_KEY = "rag-chat-onboarding-dismissed";

const onboardPanel = document.getElementById("onboarding");
const onboardSteps = document.getElementById("onboarding-steps");
const onboardHelp = document.getElementById("onboarding-help");

let onboardState = null;   // last payload from GET /onboarding
let helpOpen = false;      // the single source of truth for visibility

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
  if (!helpOpen || !onboardState) return;

  const steps = onboardState.steps || [];

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
    if (!step.done) {
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
  renderOnboarding();
}

// Called on every dashboard load. Decides whether the panel shows itself, and
// leaves an already-open panel open so a refresh mid-read does not shut it.
async function refreshOnboarding() {
  let loaded = null;
  try {
    const res = await authFetch("/onboarding");
    if (res.ok) loaded = await res.json();
  } catch (err) {
    // A checklist is not worth breaking the dashboard over.
    loaded = null;
  }
  // A body without a steps array is as useless as no body at all — auto-opening
  // on it would show an empty panel and call it a checklist.
  if (!loaded || !Array.isArray(loaded.steps)) {
    // If the panel opened during the window before this fetch landed there is
    // now nothing to put in it, and an empty shell that never fills is worse
    // than no panel — so close it.
    if (helpOpen && !onboardState) {
      helpOpen = false;
      renderOnboarding();
    }
    return;
  }
  onboardState = loaded;
  if (!helpOpen) {
    helpOpen = !onboardState.complete && !onboardDismissed();
  }
  renderOnboarding();
}

function wireOnboarding() {
  // The ? button now starts the guided tour. The checklist panel below still
  // auto-shows for someone mid-setup who does not want to be walked anywhere;
  // its close button remains the way to dismiss it.
  onboardHelp.addEventListener("click", startTour);
  document.getElementById("onboarding-close").addEventListener("click", hideOnboarding);
}
