// Which of the user's agents may answer in the active chat.
//
// Every chat starts with the whole roster and narrows by EXCLUDING agents, so
// an agent created later joins existing chats automatically. The topbar shows
// the current state rather than hiding it behind a menu: an excluded agent
// silently not answering is otherwise very hard to explain to yourself.

const scopeButton = document.getElementById("scope-button");
const scopeModal = document.getElementById("scope-modal");
const scopeList = document.getElementById("scope-list");
const scopeStatus = document.getElementById("scope-status");

// Exclusions for the chat currently open. Kept here rather than read back from
// the DOM so a failed save cannot leave the label disagreeing with the server.
let excludedAgentIds = [];

function wireScope() {
  scopeButton.addEventListener("click", openScope);
  document.getElementById("scope-cancel").addEventListener("click", () => {
    scopeModal.hidden = true;
  });
  document.getElementById("scope-save").addEventListener("click", saveScope);
  scopeModal.addEventListener("keydown", (event) => {
    if (event.key === "Escape") scopeModal.hidden = true;
  });
}

function setChatScope(excluded) {
  excludedAgentIds = Array.isArray(excluded) ? excluded : [];
  renderScopeButton();
}

function renderScopeButton() {
  // Hidden until a chat is open — there is nothing to scope otherwise.
  scopeButton.hidden = !currentSessionId;
  if (!currentSessionId) return;
  const total = agents.length;
  const active = total - excludedAgentIds.filter(
    (id) => agents.some((a) => a.id === id)
  ).length;
  scopeButton.textContent =
    excludedAgentIds.length === 0 ? "All agents" : `${active} of ${total} agents`;
}

function openScope() {
  if (!currentSessionId) return;
  scopeStatus.textContent = "";
  scopeList.innerHTML = "";
  if (agents.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "No agents yet. Create one and it will join every chat.";
    scopeList.appendChild(li);
  }
  agents.forEach((a) => {
    const li = document.createElement("li");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.id = `scope-${a.id}`;
    box.checked = !excludedAgentIds.includes(a.id);
    box.dataset.agentId = a.id;
    const label = document.createElement("label");
    label.setAttribute("for", box.id);
    label.textContent = a.name;
    li.appendChild(box);
    li.appendChild(label);
    scopeList.appendChild(li);
  });
  scopeModal.hidden = false;
}

async function saveScope() {
  const excluded = Array.from(scopeList.querySelectorAll("input[type=checkbox]"))
    .filter((box) => !box.checked)
    .map((box) => box.dataset.agentId);
  try {
    const res = await authFetch(`/sessions/${encodeURIComponent(currentSessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ excluded_agent_ids: excluded }),
    });
    let body;
    try {
      body = await res.json();
    } catch (parseErr) {
      body = { detail: `${res.status} ${res.statusText}` };
    }
    if (!res.ok) {
      scopeStatus.textContent = errorText(body, res);
      return;
    }
    // Trust the server's list, not the checkboxes: it drops ids for agents
    // that no longer exist.
    setChatScope(body.excluded_agent_ids || []);
    scopeModal.hidden = true;
  } catch (err) {
    scopeStatus.textContent = err.message;
  }
}
