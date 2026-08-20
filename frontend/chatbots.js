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
