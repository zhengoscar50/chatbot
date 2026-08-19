// A chatbot groups agents and the chats that use them. Everything the sidebar
// shows below the picker belongs to the selected one.

const CHATBOT_KEY = "rag-chat-chatbot";
let chatbots = [];
let currentChatbotId = null;

const chatbotSelect = document.getElementById("chatbot-select");

function wireChatbots() {
  chatbotSelect.addEventListener("change", async () => {
    currentChatbotId = chatbotSelect.value;
    localStorage.setItem(CHATBOT_KEY, currentChatbotId);
    currentSessionId = null;
    clearThread("Pick or create a chat to start.");
    await loadAgents();
    await loadSessions();
  });
  document.getElementById("new-chatbot").addEventListener("click", createChatbot);
  document.getElementById("delete-chatbot").addEventListener("click", deleteChatbot);
}

async function loadChatbots() {
  const res = await authFetch("/chatbots");
  if (!res.ok) return;
  chatbots = await res.json();
  const remembered = localStorage.getItem(CHATBOT_KEY);
  const exists = chatbots.some((c) => c.id === remembered);
  currentChatbotId = exists ? remembered : (chatbots[0] && chatbots[0].id) || null;
  renderChatbotSelect();
}

function renderChatbotSelect() {
  chatbotSelect.innerHTML = "";
  chatbots.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    chatbotSelect.appendChild(opt);
  });
  if (currentChatbotId) chatbotSelect.value = currentChatbotId;
}

async function createChatbot() {
  const name = prompt("Name this chatbot");
  if (!name) return;
  const res = await authFetch("/chatbots", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) return;
  const created = await res.json();
  await loadChatbots();
  currentChatbotId = created.id;
  localStorage.setItem(CHATBOT_KEY, currentChatbotId);
  renderChatbotSelect();
  await loadAgents();
  await loadSessions();
}

async function deleteChatbot() {
  if (!currentChatbotId) return;
  const bot = chatbots.find((c) => c.id === currentChatbotId);
  // Count before asking: a confirmation that names the damage is worth two
  // round trips, and "are you sure?" is not. `agents` is a module global kept
  // current by loadAgents; chats are NOT — loadSessions renders straight from
  // the response without storing them — so the count is fetched here.
  const countOf = async (path) => {
    try {
      const res = await authFetch(
        `${path}?chatbot_id=${encodeURIComponent(currentChatbotId)}`
      );
      return res.ok ? (await res.json()).length : 0;
    } catch (err) {
      return 0;
    }
  };
  const [chatCount, docCount] = await Promise.all([
    countOf("/sessions"),
    countOf("/knowledge/documents"),
  ]);
  const message =
    `Delete "${bot ? bot.name : "this chatbot"}"?\n\n` +
    `This removes ${agents.length} agents, ${chatCount} chats, ` +
    `and ${docCount} documents.\n\nThis can't be undone.`;
  if (!confirm(message)) return;
  const res = await authFetch(`/chatbots/${encodeURIComponent(currentChatbotId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    // Phase 1 returns 400 for LastChatbotError, with the message to show.
    let detail = "Could not delete this chatbot.";
    try {
      detail = (await res.json()).detail || detail;
    } catch (err) { /* keep the fallback */ }
    alert(detail);
    return;
  }
  localStorage.removeItem(CHATBOT_KEY);
  await loadChatbots();
  currentSessionId = null;
  clearThread("Pick or create a chat to start.");
  await loadAgents();
  await loadSessions();
}
