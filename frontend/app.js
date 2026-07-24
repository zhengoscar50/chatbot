const userInput = document.getElementById("user-input");
const sidebarStatus = document.getElementById("sidebar-status");
const sessionList = document.getElementById("session-list");
const newSessionButton = document.getElementById("new-session");
const sidebar = document.getElementById("sidebar");
const sidebarToggle = document.getElementById("sidebar-toggle");
const activeTitle = document.getElementById("active-title");
const attachButton = document.getElementById("attach-button");
const fileInput = document.getElementById("file-input");
const attachmentChip = document.getElementById("attachment-chip");
const attachmentName = document.getElementById("attachment-name");
const attachmentStatus = document.getElementById("attachment-status");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendButton = document.getElementById("send-button");
const messages = document.getElementById("messages");

const USER_KEY = "rag-chat-user";
let currentUser = null;
let currentSessionId = null;
let isAsking = false;

init();

function init() {
  setComposerEnabled(false);
  const saved = localStorage.getItem(USER_KEY);
  if (saved) {
    userInput.value = saved;
    switchUser(saved);
  }
  userInput.addEventListener("change", () => switchUser(userInput.value));
  userInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      switchUser(userInput.value);
    }
  });
  newSessionButton.addEventListener("click", createSession);
  sidebarToggle.addEventListener("click", () => sidebar.classList.toggle("open"));
}

async function switchUser(rawName) {
  const name = rawName.trim();
  currentSessionId = null;
  clearThread("Pick or create a session to start.");
  setComposerEnabled(false);
  activeTitle.textContent = "RAG Chat";
  if (!name) {
    currentUser = null;
    newSessionButton.disabled = true;
    sessionList.innerHTML = "";
    setSidebarStatus("Enter a user name to start", null);
    return;
  }
  currentUser = name;
  localStorage.setItem(USER_KEY, name);
  newSessionButton.disabled = false;
  await loadSessions();
}

async function loadSessions() {
  setSidebarStatus("Loading sessions…", null);
  try {
    const response = await fetch(`/sessions?user=${encodeURIComponent(currentUser)}`);
    const body = await response.json();
    if (!response.ok) {
      setSidebarStatus(body.detail || response.statusText, "error");
      return;
    }
    renderSessionList(body);
    setSidebarStatus(body.length ? "" : "No sessions yet — create one.", null);
  } catch (err) {
    setSidebarStatus(err.message, "error");
  }
}

function renderSessionList(sessions) {
  sessionList.innerHTML = "";
  sessions.forEach((s) => {
    const li = document.createElement("li");
    li.textContent = s.name;
    li.dataset.id = s.id;
    if (s.id === currentSessionId) li.classList.add("active");
    li.addEventListener("click", () => openSession(s.id, s.name));
    sessionList.appendChild(li);
  });
}

async function createSession() {
  if (!currentUser) return;
  try {
    const response = await fetch("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: currentUser }),
    });
    const body = await response.json();
    if (!response.ok) {
      setSidebarStatus(body.detail || response.statusText, "error");
      return;
    }
    await loadSessions();
    openSession(body.id, body.name);
  } catch (err) {
    setSidebarStatus(err.message, "error");
  }
}

async function openSession(id, name) {
  currentSessionId = id;
  activeTitle.textContent = name;
  attachmentChip.hidden = true;
  sidebar.classList.remove("open");
  markActive();
  setComposerEnabled(true);
  clearThread("Upload a PDF, then ask about it — or just ask.");

  try {
    const response = await fetch(`/sessions/${id}/messages`);
    const body = await response.json();
    if (response.ok && body.messages && body.messages.length) {
      messages.innerHTML = "";
      body.messages.forEach((m) => {
        if (m.role === "user") appendMessage("user", null, m.text);
        else appendMessage("assistant", "AI", m.text, m.citations);
      });
    }
  } catch (err) {
    appendMessage("error", "!", err.message);
  }
}

function markActive() {
  Array.from(sessionList.children).forEach((li) => {
    li.classList.toggle("active", li.dataset.id === currentSessionId);
  });
}

attachButton.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  if (!currentSessionId) {
    fileInput.value = "";
    return;
  }
  showAttachment(file.name, "Uploading and indexing…", null);
  const formData = new FormData();
  formData.append("file", file);
  formData.append("session_id", currentSessionId);
  try {
    const response = await fetch("/ingest/file", { method: "POST", body: formData });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok || response.status === 202) {
      showAttachment(file.name, body.status, body.status === "indexed" ? "ok" : null);
    } else {
      showAttachment(file.name, body.detail || response.statusText, "error");
    }
  } catch (err) {
    showAttachment(file.name, err.message, "error");
  }
  fileInput.value = "";
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (isAsking) return;
  const query = chatInput.value.trim();
  if (!query) return;
  if (!currentSessionId) {
    appendMessage("error", "!", "Pick or create a session first.");
    return;
  }
  appendMessage("user", null, query);
  chatInput.value = "";

  isAsking = true;
  sendButton.disabled = true;
  const thinking = appendThinking();
  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: currentSessionId, query }),
    });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok) {
      appendMessage("assistant", "AI", body.answer, body.citations);
      loadSessions(); // refresh titles/order (first message names the session)
    } else {
      appendMessage("error", "!", body.detail || response.statusText);
    }
  } catch (err) {
    appendMessage("error", "!", err.message);
  } finally {
    thinking.remove();
    isAsking = false;
    sendButton.disabled = false;
  }
});

function setComposerEnabled(enabled) {
  chatInput.disabled = !enabled;
  sendButton.disabled = !enabled;
  attachButton.disabled = !enabled;
}

function setSidebarStatus(text, state) {
  sidebarStatus.textContent = text;
  if (state) sidebarStatus.dataset.state = state;
  else delete sidebarStatus.dataset.state;
}

function clearThread(note) {
  messages.innerHTML = "";
  const el = document.createElement("div");
  el.className = "empty-state";
  el.textContent = note;
  messages.appendChild(el);
}

function showAttachment(name, statusText, state) {
  attachmentChip.hidden = false;
  attachmentName.textContent = name;
  attachmentStatus.textContent = statusText;
  if (state) attachmentStatus.dataset.state = state;
  else delete attachmentStatus.dataset.state;
}

function appendThinking() {
  const existingEmpty = messages.querySelector(".empty-state");
  if (existingEmpty) existingEmpty.remove();
  const row = document.createElement("div");
  row.className = "row row--assistant";
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.textContent = "AI";
  row.appendChild(avatar);
  const content = document.createElement("div");
  content.className = "content";
  const thinking = document.createElement("div");
  thinking.className = "thinking";
  thinking.setAttribute("aria-label", "Thinking");
  for (let i = 0; i < 3; i++) thinking.appendChild(document.createElement("span"));
  content.appendChild(thinking);
  row.appendChild(content);
  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

function appendMessage(role, avatarText, text, citations) {
  const existingEmpty = messages.querySelector(".empty-state");
  if (existingEmpty) existingEmpty.remove();

  const row = document.createElement("div");
  row.className = `row row--${role}`;

  if (role === "user") {
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const p = document.createElement("p");
    p.textContent = text;
    bubble.appendChild(p);
    row.appendChild(bubble);
  } else if (role === "system") {
    const content = document.createElement("div");
    content.className = "content";
    const p = document.createElement("p");
    p.textContent = text;
    content.appendChild(p);
    row.appendChild(content);
  } else {
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = avatarText;
    row.appendChild(avatar);
    const content = document.createElement("div");
    content.className = "content";
    const p = document.createElement("p");
    p.textContent = text;
    content.appendChild(p);
    if (citations && citations.length > 0) {
      content.appendChild(buildReferenceList(citations));
    }
    row.appendChild(content);
  }

  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
}

function buildReferenceList(citations) {
  const list = document.createElement("ul");
  list.className = "refs";
  citations.forEach((citation, index) => {
    const item = document.createElement("li");
    if (citation.text_excerpt) item.title = citation.text_excerpt;
    const tag = document.createElement("span");
    tag.className = "ref__tag";
    tag.textContent = `[${citation.key || index + 1}]`;
    item.appendChild(tag);
    const name = citation.source_name || citation.source_id || "source";
    item.appendChild(document.createTextNode(` ${name}`));
    list.appendChild(item);
  });
  return list;
}
