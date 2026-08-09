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

const authGate = document.getElementById("auth-gate");
const authForm = document.getElementById("auth-form");
const authUsernameInput = document.getElementById("auth-username");
const authPasswordInput = document.getElementById("auth-password");
const authInviteInput = document.getElementById("auth-invite");
const authSubmit = document.getElementById("auth-submit");
const authError = document.getElementById("auth-error");
const authToggle = document.getElementById("auth-toggle");
const authModeLabel = document.getElementById("auth-mode-label");
const currentUserLabel = document.getElementById("current-user");

const TOKEN_KEY = "rag-chat-token";
const NAME_KEY = "rag-chat-username";
let authToken = null;
let currentUsername = null;
let authMode = "login"; // or "register"
// Deployments that share a public URL require an invite code to register,
// so a forwarded link alone cannot spend the owner's LLM credits. Local
// development leaves it unset and the field never appears.
let inviteRequired = false;
let currentSessionId = null;
let isAsking = false;
let uploadPollToken = 0;

init();

function init() {
  setComposerEnabled(false);
  authToken = localStorage.getItem(TOKEN_KEY);
  currentUsername = localStorage.getItem(NAME_KEY);
  wireAuthForm();
  loadSignupPolicy();
  wireAgents();
  newSessionButton.addEventListener("click", createSession);
  sidebarToggle.addEventListener("click", () => sidebar.classList.toggle("open"));
  document.getElementById("logout-btn").addEventListener("click", doLogout);
  if (authToken) enterApp();
  else showAuthGate();
}

async function authFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    doLogout();
    throw new Error("Session expired — please log in again.");
  }
  return response;
}

async function loadSignupPolicy() {
  try {
    const res = await fetch("/auth/signup-policy");
    if (!res.ok) return;
    inviteRequired = Boolean((await res.json()).invite_required);
  } catch (err) {
    // Leave the field hidden; a wrong guess here only costs a clear 403.
  }
}

function wireAuthForm() {
  authToggle.addEventListener("click", () => {
    authMode = authMode === "login" ? "register" : "login";
    setAuthError("");
    authInviteInput.hidden = !(inviteRequired && authMode === "register");
    if (authMode === "register") {
      authModeLabel.textContent = "Create an account";
      authSubmit.textContent = "Register";
      authToggle.textContent = "Already have an account? Log in";
    } else {
      authModeLabel.textContent = "Log in to continue";
      authSubmit.textContent = "Log in";
      authToggle.textContent = "Need an account? Register";
    }
  });

  authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = authUsernameInput.value.trim();
    const password = authPasswordInput.value;
    if (!username || !password) {
      setAuthError("Enter a username and password.");
      return;
    }
    setAuthError("");
    authSubmit.disabled = true;
    try {
      const endpoint = authMode === "register" ? "/auth/register" : "/auth/login";
      const payload = { username, password };
      if (authMode === "register" && inviteRequired) {
        payload.invite_code = authInviteInput.value.trim();
      }
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      let body;
      try {
        body = await response.json();
      } catch (parseErr) {
        body = { detail: `${response.status} ${response.statusText}` };
      }
      if (!response.ok) {
        setAuthError(errorText(body, response));
        return;
      }
      authToken = body.token;
      currentUsername = body.username || username;
      localStorage.setItem(TOKEN_KEY, authToken);
      localStorage.setItem(NAME_KEY, currentUsername);
      authPasswordInput.value = "";
      enterApp();
    } catch (err) {
      setAuthError(err.message);
    } finally {
      authSubmit.disabled = false;
    }
  });
}

// Turn an error response body into a readable string. FastAPI returns `detail`
// as a plain string for most 4xx, but as a LIST of {loc,msg,...} objects for
// pydantic 422 validation errors — joining those `msg`s avoids "[object Object]".
function errorText(body, response) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((e) => (e && e.msg ? e.msg : String(e))).join("; ");
  }
  if (detail && typeof detail === "object" && detail.msg) return detail.msg;
  return (response && response.statusText) || "Request failed";
}

function setAuthError(text) {
  authError.textContent = text;
}

function showAuthGate() {
  authGate.hidden = false;
  setComposerEnabled(false);
}

async function enterApp() {
  authGate.hidden = true;
  currentUserLabel.textContent = currentUsername || "";
  newSessionButton.disabled = false;
  setComposerEnabled(true);
  clearThread("Type a message to start a chat.");
  // The roster is loaded for the Manage agents list; chats are independent of
  // it now, so it never touches the thread.
  await loadAgents();
  loadSessions();
}

function doLogout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(NAME_KEY);
  authToken = null;
  currentUsername = null;
  currentSessionId = null;
  sessionList.innerHTML = "";
  attachmentChip.hidden = true;
  activeTitle.textContent = "RAG Chat";
  resetAgentState();
  authUsernameInput.value = "";
  authPasswordInput.value = "";
  setAuthError("");
  showAuthGate();
}

// Return the active session id, creating a new session on the fly if none is
// selected (so you can just start typing without clicking "New chat").
async function ensureSession() {
  if (currentSessionId) return currentSessionId;
  const response = await authFetch("/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(errorText(body, response));
  currentSessionId = body.id;
  activeTitle.textContent = body.name;
  await loadSessions();
  return currentSessionId;
}

async function loadSessions() {
  setSidebarStatus("Loading sessions…", null);
  try {
    const response = await authFetch("/sessions");
    const body = await response.json();
    if (!response.ok) {
      setSidebarStatus(errorText(body, response), "error");
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
    li.dataset.id = s.id;
    if (s.id === currentSessionId) li.classList.add("active");

    const nameSpan = document.createElement("span");
    nameSpan.className = "session-name";
    nameSpan.textContent = s.name;
    li.appendChild(nameSpan);

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "session-edit";
    editBtn.title = "Rename";
    editBtn.setAttribute("aria-label", `Rename ${s.name}`);
    editBtn.textContent = "✎";
    editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      startRename(li, s);
    });
    li.appendChild(editBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "session-delete";
    deleteBtn.title = "Delete";
    deleteBtn.setAttribute("aria-label", `Delete ${s.name}`);
    deleteBtn.textContent = "🗑";
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s.id, s.name);
    });
    li.appendChild(deleteBtn);

    li.addEventListener("click", () => openSession(s.id, s.name));
    sessionList.appendChild(li);
  });
}

async function deleteSession(id, name) {
  if (!window.confirm(`Delete "${name}"? This permanently removes its documents and chat history.`)) {
    return;
  }
  try {
    const response = await authFetch(`/sessions/${id}`, { method: "DELETE" });
    if (!response.ok && response.status !== 204) {
      const body = await response.json().catch(() => ({}));
      setSidebarStatus(errorText(body, response), "error");
      return;
    }
    if (id === currentSessionId) {
      currentSessionId = null;
      activeTitle.textContent = "RAG Chat";
      attachmentChip.hidden = true;
      clearThread("Type a message to start a new session — or pick one on the left.");
    }
    await loadSessions();
  } catch (err) {
    setSidebarStatus(err.message, "error");
  }
}

function startRename(li, session) {
  const input = document.createElement("input");
  input.className = "session-rename";
  input.value = session.name;
  li.innerHTML = "";
  li.appendChild(input);
  input.focus();
  input.select();

  let done = false;
  const commit = async () => {
    if (done) return;
    done = true;
    const newName = input.value.trim();
    if (!newName || newName === session.name) {
      loadSessions();
      return;
    }
    try {
      const response = await authFetch(`/sessions/${session.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        setSidebarStatus(errorText(body, response), "error");
      } else if (session.id === currentSessionId) {
        activeTitle.textContent = newName;
      }
    } catch (err) {
      setSidebarStatus(err.message, "error");
    }
    loadSessions();
  };

  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("blur", commit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      done = true;
      loadSessions();
    }
  });
}

async function createSession() {
  if (!authToken || newSessionButton.disabled) return;
  newSessionButton.disabled = true;
  try {
    const response = await authFetch("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const body = await response.json();
    if (!response.ok) {
      setSidebarStatus(errorText(body, response), "error");
      return;
    }
    await loadSessions();
    openSession(body.id, body.name);
  } catch (err) {
    setSidebarStatus(err.message, "error");
  } finally {
    newSessionButton.disabled = false;
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
    const response = await authFetch(`/sessions/${id}/messages`);
    const body = await response.json();
    if (response.ok && body.messages && body.messages.length) {
      messages.innerHTML = "";
      body.messages.forEach((m) => {
        if (m.role === "user") appendMessage("user", null, m.text);
        else appendMessage("assistant", "AI", m.text, m.citations, 
                           m.answered_by ? { name: m.answered_by } : null);
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
  uploadPollToken++;
  if (!authToken) {
    fileInput.value = "";
    return;
  }
  let sessionId;
  try {
    sessionId = await ensureSession();
  } catch (err) {
    showAttachment(file.name, err.message, "error");
    fileInput.value = "";
    return;
  }
  showAttachment(file.name, "Uploading and indexing…", null);
  const formData = new FormData();
  formData.append("file", file);
  formData.append("session_id", sessionId);
  try {
    const response = await authFetch("/ingest/file", { method: "POST", body: formData });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok || response.status === 202) {
      showAttachment(file.name, "Indexing…", null);
      pollIngestStatus(sessionId, body.source_id, file.name);
    } else {
      showAttachment(file.name, errorText(body, response), "error");
    }
  } catch (err) {
    showAttachment(file.name, err.message, "error");
  }
  fileInput.value = "";
});

async function pollIngestStatus(sessionId, sourceId, fileName) {
  const myToken = ++uploadPollToken;
  const started = Date.now();
  while (myToken === uploadPollToken) {
    if (Date.now() - started > 10 * 60 * 1000) {
      showAttachment(fileName, "Still processing — check back shortly.", null);
      return;
    }
    await new Promise((r) => setTimeout(r, 3000));
    if (myToken !== uploadPollToken) return;
    try {
      const res = await authFetch(`/ingest/status/${encodeURIComponent(sourceId)}?session_id=${encodeURIComponent(sessionId)}`);
      if (!res.ok) continue; // transient — keep polling
      const body = await res.json();
      if (myToken !== uploadPollToken) return;
      if (body.status === "indexed") {
        showAttachment(fileName, "indexed", "ok");
        return;
      }
      if (body.status === "failed") {
        showAttachment(fileName, body.detail || "Indexing failed.", "error");
        return;
      }
      // processing -> keep polling
    } catch (err) {
      // transient network error -> keep polling
    }
  }
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (isAsking) return;
  const query = chatInput.value.trim();
  if (!query) return;
  if (!authToken) {
    appendMessage("error", "!", "Log in first.");
    return;
  }
  let sessionId;
  try {
    sessionId = await ensureSession();
  } catch (err) {
    appendMessage("error", "!", err.message);
    return;
  }
  appendMessage("user", null, query);
  chatInput.value = "";

  isAsking = true;
  sendButton.disabled = true;
  const thinking = appendThinking();
  try {
    const response = await authFetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, query }),
    });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok) {
      appendMessage("assistant", "AI", body.answer, body.citations, body.answered_by);
      loadSessions(); // refresh titles/order (first message names the session)
    } else {
      appendMessage("error", "!", errorText(body, response));
    }
  } catch (err) {
    appendMessage("error", "!", err.message);
  } finally {
    thinking.remove();
    isAsking = false;
    sendButton.disabled = false;
  }
});

function clearChildren(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

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

// `answeredBy` names the agent the orchestrator picked. Showing it is the
// only feedback loop for mis-routing — a wrong pick is fixable only if it is
// visible.
function appendMessage(role, avatarText, text, citations, answeredBy) {
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
    if (answeredBy && answeredBy.name) {
      const badge = document.createElement("span");
      badge.className = "agent-badge";
      badge.textContent = answeredBy.name;
      content.appendChild(badge);
    }
    row.appendChild(content);
  }

  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
}

// Handles two citation shapes: chat's {key, source_name/source_id, text_excerpt}
// objects.
function buildReferenceList(citations) {
  const list = document.createElement("ul");
  list.className = "refs";
  citations.forEach((citation, index) => {
    const item = document.createElement("li");
    const tag = document.createElement("span");
    tag.className = "ref__tag";
    let name;
    if (typeof citation === "string") {
      tag.textContent = `[${index + 1}]`;
      name = citation;
    } else {
      if (citation.text_excerpt) item.title = citation.text_excerpt;
      tag.textContent = `[${citation.key || index + 1}]`;
      name = citation.source_name || citation.source_id || "source";
    }
    item.appendChild(tag);
    item.appendChild(document.createTextNode(` ${name}`));
    list.appendChild(item);
  });
  return list;
}
