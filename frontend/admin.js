const gateForm = document.getElementById("gate-form");
const passwordInput = document.getElementById("password-input");
const gateStatus = document.getElementById("gate-status");
const gate = document.getElementById("gate");
const usersPanel = document.getElementById("users-panel");
const usersTable = document.getElementById("users-table");
const usersStatus = document.getElementById("users-status");
const usersRefresh = document.getElementById("users-refresh");

let adminPassword = null;

gateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = passwordInput.value;
  if (!password) return;
  gateStatus.textContent = "Checking…";
  gateStatus.removeAttribute("data-state");
  try {
    const response = await fetch("/admin/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (response.ok) {
      adminPassword = password;
      gate.hidden = true;
      usersPanel.hidden = false;
      loadUsers();
    } else if (response.status === 401) {
      setStatus(gateStatus, "Incorrect password.", "error");
    } else if (response.status === 403) {
      setStatus(gateStatus, "Admin is not configured (set ADMIN_PASSWORD).", "error");
    } else {
      const body = await response.json().catch(() => ({}));
      setStatus(gateStatus, body.detail || response.statusText, "error");
    }
  } catch (err) {
    setStatus(gateStatus, err.message, "error");
  }
});

function setStatus(el, text, state) {
  el.textContent = text;
  if (state) el.dataset.state = state;
  else delete el.dataset.state;
}

// ---- Users panel ----------------------------------------------------

usersRefresh.addEventListener("click", loadUsers);

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

// Wraps fetch with the X-Admin-Password header every /admin data endpoint
// needs. On a 401 the password is no longer valid (wrong, or the gate hasn't
// been unlocked yet) — reset the UI back to the gate.
async function adminFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  headers["X-Admin-Password"] = adminPassword;
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    resetToGate();
    throw new Error("Session expired — please unlock again.");
  }
  return response;
}

function resetToGate() {
  adminPassword = null;
  gate.hidden = false;
  usersPanel.hidden = true;
  setStatus(gateStatus, "Session expired — please unlock again.", "error");
}

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

async function loadUsers() {
  if (!adminPassword) return;
  setStatus(usersStatus, "Loading users…", null);
  try {
    const response = await adminFetch("/admin/users");
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = [];
    }
    if (!response.ok) {
      setStatus(usersStatus, errorText(body, response), "error");
      return;
    }
    renderUsersTable(body);
    setStatus(usersStatus, body.length ? "" : "No users yet.", null);
  } catch (err) {
    setStatus(usersStatus, err.message, "error");
  }
}

function renderUsersTable(users) {
  usersTable.innerHTML = "";
  users.forEach((user) => {
    usersTable.appendChild(buildUserRow(user));
  });
}

function buildUserRow(user) {
  const row = document.createElement("div");
  row.className = "user-row";
  row.dataset.id = user.id;

  const main = document.createElement("div");
  main.className = "user-row__main";

  const nameSpan = document.createElement("span");
  nameSpan.className = "user-row__name";
  nameSpan.textContent = user.username;
  main.appendChild(nameSpan);

  const metaSpan = document.createElement("span");
  metaSpan.className = "user-row__meta";
  const count = user.session_count;
  metaSpan.textContent = `Created ${formatDate(user.created_at)} · ${count} session${count === 1 ? "" : "s"}`;
  main.appendChild(metaSpan);

  const sessionsContainer = document.createElement("div");
  sessionsContainer.className = "user-sessions";
  sessionsContainer.hidden = true;

  const actions = document.createElement("div");
  actions.className = "user-row__actions";

  const sessionsBtn = document.createElement("button");
  sessionsBtn.type = "button";
  sessionsBtn.className = "btn-small";
  sessionsBtn.textContent = "Sessions";
  sessionsBtn.addEventListener("click", () => toggleSessions(user, sessionsContainer));
  actions.appendChild(sessionsBtn);

  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "btn-small";
  resetBtn.textContent = "Reset PW";
  resetBtn.addEventListener("click", () => resetPassword(user));
  actions.appendChild(resetBtn);

  const renameBtn = document.createElement("button");
  renameBtn.type = "button";
  renameBtn.className = "btn-small";
  renameBtn.textContent = "Rename";
  renameBtn.addEventListener("click", () => renameUser(user));
  actions.appendChild(renameBtn);

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "btn-small btn-small--danger";
  deleteBtn.textContent = "Delete";
  deleteBtn.addEventListener("click", () => deleteUser(user));
  actions.appendChild(deleteBtn);

  main.appendChild(actions);
  row.appendChild(main);
  row.appendChild(sessionsContainer);
  return row;
}

async function toggleSessions(user, container) {
  if (!container.hidden) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  if (container.dataset.loaded === "true") return;

  container.textContent = "";
  container.appendChild(buildStatusLine("Loading sessions…", "user-sessions__status"));
  try {
    const response = await adminFetch(`/admin/users/${user.id}/sessions`);
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = [];
    }
    container.textContent = "";
    if (!response.ok) {
      container.appendChild(buildStatusLine(errorText(body, response), "user-sessions__status user-sessions__status--error"));
      return;
    }
    container.dataset.loaded = "true";
    renderSessions(body, container);
  } catch (err) {
    container.textContent = "";
    container.appendChild(buildStatusLine(err.message, "user-sessions__status user-sessions__status--error"));
  }
}

function renderSessions(sessions, container) {
  if (!sessions.length) {
    container.appendChild(buildStatusLine("No sessions.", "user-sessions__status"));
    return;
  }
  sessions.forEach((session) => {
    const item = document.createElement("div");
    item.className = "user-session";

    const head = document.createElement("div");
    head.className = "user-session__head";

    const name = document.createElement("span");
    name.className = "user-session__name";
    name.textContent = session.name;
    head.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "user-session__meta";
    meta.textContent = `Updated ${formatDate(session.updated_at)}`;
    head.appendChild(meta);

    const messagesContainer = document.createElement("div");
    messagesContainer.className = "user-messages";
    messagesContainer.hidden = true;

    const readBtn = document.createElement("button");
    readBtn.type = "button";
    readBtn.className = "btn-small";
    readBtn.textContent = "Read";
    readBtn.addEventListener("click", () => toggleMessages(session, messagesContainer));
    head.appendChild(readBtn);

    item.appendChild(head);
    item.appendChild(messagesContainer);
    container.appendChild(item);
  });
}

async function toggleMessages(session, container) {
  if (!container.hidden) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  if (container.dataset.loaded === "true") return;

  container.textContent = "";
  container.appendChild(buildStatusLine("Loading messages…", "user-messages__status"));
  try {
    const response = await adminFetch(`/admin/sessions/${session.id}/messages`);
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = {};
    }
    container.textContent = "";
    if (!response.ok) {
      container.appendChild(buildStatusLine(errorText(body, response), "user-messages__status user-messages__status--error"));
      return;
    }
    container.dataset.loaded = "true";
    renderMessages(body.messages || [], container);
  } catch (err) {
    container.textContent = "";
    container.appendChild(buildStatusLine(err.message, "user-messages__status user-messages__status--error"));
  }
}

function renderMessages(messages, container) {
  if (!messages.length) {
    container.appendChild(buildStatusLine("No messages.", "user-messages__status"));
    return;
  }
  messages.forEach((m) => {
    const p = document.createElement("p");
    p.className = "user-message";
    const role = document.createElement("strong");
    role.textContent = `${m.role}: `;
    p.appendChild(role);
    p.appendChild(document.createTextNode(m.text));
    container.appendChild(p);
  });
}

function buildStatusLine(text, className) {
  const p = document.createElement("p");
  p.className = className;
  p.textContent = text;
  return p;
}

async function resetPassword(user) {
  const password = window.prompt(`New password for ${user.username} (8+ chars):`);
  if (password === null) return;
  if (password.length < 8) {
    setStatus(usersStatus, "Password must be at least 8 characters.", "error");
    return;
  }
  setStatus(usersStatus, `Resetting password for ${user.username}…`, null);
  try {
    const response = await adminFetch(`/admin/users/${user.id}/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (response.ok || response.status === 204) {
      setStatus(usersStatus, `Password reset for ${user.username}.`, "ok");
    } else {
      const body = await response.json().catch(() => ({}));
      setStatus(usersStatus, errorText(body, response), "error");
    }
  } catch (err) {
    setStatus(usersStatus, err.message, "error");
  }
}

async function renameUser(user) {
  const newUsername = window.prompt("New username:", user.username);
  if (newUsername === null) return;
  const trimmed = newUsername.trim();
  if (!trimmed || trimmed === user.username) return;
  setStatus(usersStatus, `Renaming ${user.username}…`, null);
  try {
    const response = await adminFetch(`/admin/users/${user.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: trimmed }),
    });
    if (response.ok) {
      setStatus(usersStatus, `Renamed to ${trimmed}.`, "ok");
      loadUsers();
    } else {
      const body = await response.json().catch(() => ({}));
      setStatus(usersStatus, errorText(body, response), "error");
    }
  } catch (err) {
    setStatus(usersStatus, err.message, "error");
  }
}

async function deleteUser(user) {
  if (!window.confirm(`Delete ${user.username} and ALL their sessions/data?`)) return;
  setStatus(usersStatus, `Deleting ${user.username}…`, null);
  try {
    const response = await adminFetch(`/admin/users/${user.id}`, { method: "DELETE" });
    if (response.ok || response.status === 204) {
      setStatus(usersStatus, `Deleted ${user.username}.`, "ok");
      loadUsers();
    } else {
      const body = await response.json().catch(() => ({}));
      setStatus(usersStatus, errorText(body, response), "error");
    }
  } catch (err) {
    setStatus(usersStatus, err.message, "error");
  }
}
