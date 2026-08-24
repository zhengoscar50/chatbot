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
    // The panel this step works inside. Opening it is not the step; finishing
    // with it is. See `opens` handling in tour.js.
    opens: "#agent-modal",
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
    doing: "Open Chatbot knowledge. A PDF you add there is readable by every "
         + "agent in this chatbot, including the general assistant.",
    showing: "Documents here are readable by every agent in this chatbot, "
           + "including the general assistant.",
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
    target: ".agent-badge:not(.agent-badge--general)",
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
    fallbackTarget: ".agent-badge--general",
    fallbackTitle: "The general assistant answered",
    fallbackBody: "No specialist was picked, so no document was searched. "
                + "That almost always means an agent's description does not "
                + "match the question.",
    fallbackAction: { label: "Check the description", stepId: "description" },
    // And when nothing has been asked at all — someone walked past the ask
    // step with Next. Claiming "a specialist answered" over an empty chat
    // would be the tour asserting something that plainly did not happen.
    emptyTitle: "Nothing asked yet",
    emptyBody: "Send a question and a badge under the answer names whichever "
             + "agent handled it. That badge is how you tell routing worked.",
  },
  {
    id: "attach",
    surface: "chat",
    target: "#attach-button",
    needs: null,
    title: "A document for one chat",
    doing: "Attach a PDF here and only this chat can read it. It goes when the "
         + "chat goes — unless you save it to the chatbot's knowledge, which "
         + "the chip offers once it has uploaded.",
    showing: "Attach a PDF here and only this chat can read it. It goes when "
           + "the chat goes — unless you save it to the chatbot's knowledge, "
           + "which the chip offers once it has uploaded.",
  },
  {
    id: "scope",
    surface: "chat",
    target: "#scope-button",
    needs: null,
    title: "Choose who answers",
    doing: "Limit a chat to certain agents. Worth doing when a question could "
         + "match two specialists and you already know which one you want.",
    showing: "Limit a chat to certain agents. Worth doing when a question could "
           + "match two specialists and you already know which one you want.",
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
