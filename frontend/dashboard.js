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
