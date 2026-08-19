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
