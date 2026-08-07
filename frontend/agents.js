// Agent management: list, create, edit, train, delete.
//
// Kept out of app.js, which already carries auth, sessions, chat, uploads and
// rendering. Loaded before app.js; app.js calls wireAgents() and loadAgents(),
// and this file calls back into onAgentChanged()/authFetch()/errorText().

let agents = [];
let currentAgentId = null;
let editingAgentId = null;

const agentSelect = document.getElementById("agent-select");
const agentModal = document.getElementById("agent-modal");
const agentForm = document.getElementById("agent-form");
const agentNameInput = document.getElementById("agent-name");
const agentInstructionsInput = document.getElementById("agent-instructions");
const agentModelInput = document.getElementById("agent-model");
const agentGroundingInput = document.getElementById("agent-grounding");
const agentGeneralKbInput = document.getElementById("agent-general-kb");
const agentDocsSection = document.getElementById("agent-docs");
const agentDocList = document.getElementById("agent-doc-list");
const agentTrainFile = document.getElementById("agent-train-file");
const agentTrainStatus = document.getElementById("agent-train-status");
const agentError = document.getElementById("agent-error");
const agentDeleteButton = document.getElementById("agent-delete");
const agentModalTitle = document.getElementById("agent-modal-title");
const manageAgentButton = document.getElementById("manage-agent");

function wireAgents() {
  document.getElementById("new-agent").addEventListener("click", () => openAgentModal(null));
  manageAgentButton.addEventListener("click", () => {
    if (currentAgentId) openAgentModal(currentAgentId);
  });
  document.getElementById("agent-cancel").addEventListener("click", closeAgentModal);
  agentForm.addEventListener("submit", saveAgent);
  agentDeleteButton.addEventListener("click", deleteAgent);
  agentTrainFile.addEventListener("change", trainAgent);
  agentSelect.addEventListener("change", () => {
    currentAgentId = agentSelect.value || null;
    onAgentChanged();
  });
  agentModal.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAgentModal();
  });
}

async function loadAgents() {
  try {
    const res = await authFetch("/agents");
    if (!res.ok) return;
    agents = await res.json();
  } catch (err) {
    return;
  }
  if (!agents.some((a) => a.id === currentAgentId)) {
    currentAgentId = agents.length > 0 ? agents[0].id : null;
  }
  renderAgentSelect();
  agentSelect.value = currentAgentId || "";
  manageAgentButton.disabled = !currentAgentId;
  onAgentChanged();
}

function renderAgentSelect() {
  agentSelect.innerHTML = "";
  if (agents.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No agents yet — click +";
    agentSelect.appendChild(opt);
    return;
  }
  agents.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = a.trained ? a.name : `${a.name} (untrained)`;
    agentSelect.appendChild(opt);
  });
}

function openAgentModal(agentId) {
  editingAgentId = agentId;
  agentError.textContent = "";
  agentTrainStatus.textContent = "";
  agentModalTitle.textContent = agentId ? "Manage agent" : "New agent";
  agentDeleteButton.hidden = !agentId;
  agentDocsSection.hidden = !agentId;
  if (agentId) {
    loadAgentDetail(agentId);
    loadAgentDocuments(agentId);
  } else {
    agentNameInput.value = "";
    agentInstructionsInput.value = "";
    agentModelInput.value = "";
    agentGroundingInput.value = "strict";
    agentGeneralKbInput.checked = false;
  }
  agentModal.hidden = false;
  agentNameInput.focus();
}

function closeAgentModal() {
  agentModal.hidden = true;
  editingAgentId = null;
}

async function loadAgentDetail(agentId) {
  const res = await authFetch(`/agents/${encodeURIComponent(agentId)}`);
  if (!res.ok) return;
  const a = await res.json();
  agentNameInput.value = a.name;
  agentInstructionsInput.value = a.instructions || "";
  agentModelInput.value = a.model || "";
  agentGroundingInput.value = a.grounding;
  agentGeneralKbInput.checked = a.use_general_kb;
}

async function saveAgent(event) {
  event.preventDefault();
  agentError.textContent = "";
  const payload = {
    name: agentNameInput.value.trim(),
    instructions: agentInstructionsInput.value,
    grounding: agentGroundingInput.value,
    use_general_kb: agentGeneralKbInput.checked,
  };
  const model = agentModelInput.value.trim();
  if (model) payload.model = model;

  const url = editingAgentId ? `/agents/${encodeURIComponent(editingAgentId)}` : "/agents";
  try {
    const res = await authFetch(url, {
      method: editingAgentId ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    let body;
    try {
      body = await res.json();
    } catch (parseErr) {
      body = { detail: `${res.status} ${res.statusText}` };
    }
    if (!res.ok) {
      agentError.textContent = errorText(body, res);
      return;
    }
    if (!editingAgentId) currentAgentId = body.id;
    closeAgentModal();
    await loadAgents();
  } catch (err) {
    agentError.textContent = err.message;
  }
}

async function deleteAgent() {
  if (!editingAgentId) return;
  const agent = agents.find((a) => a.id === editingAgentId);
  const label = agent ? agent.name : "this agent";
  const confirmed = window.confirm(
    `Delete ${label}? Its training and every chat with it are removed. This cannot be undone.`
  );
  if (!confirmed) return;
  try {
    const res = await authFetch(`/agents/${encodeURIComponent(editingAgentId)}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      agentError.textContent = "Could not delete this agent.";
      return;
    }
    currentAgentId = null;
    closeAgentModal();
    await loadAgents();
  } catch (err) {
    agentError.textContent = err.message;
  }
}

async function loadAgentDocuments(agentId) {
  agentDocList.innerHTML = "";
  const res = await authFetch(`/agents/${encodeURIComponent(agentId)}/documents`);
  if (!res.ok) return;
  const docs = await res.json();
  if (docs.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "Nothing yet — add a document below.";
    agentDocList.appendChild(li);
    return;
  }
  docs.forEach((d) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = d.filename || d.source_id;
    li.appendChild(name);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "link-danger";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => untrainDocument(agentId, d.source_id));
    li.appendChild(remove);
    agentDocList.appendChild(li);
  });
}

async function untrainDocument(agentId, sourceId) {
  const res = await authFetch(
    `/agents/${encodeURIComponent(agentId)}/documents/${encodeURIComponent(sourceId)}`,
    { method: "DELETE" }
  );
  if (res.ok) {
    await loadAgentDocuments(agentId);
    await loadAgents();
  }
}

async function trainAgent() {
  const file = agentTrainFile.files[0];
  if (!file || !editingAgentId) return;
  agentTrainStatus.textContent = `Training on ${file.name}…`;
  const data = new FormData();
  data.append("file", file);
  try {
    const res = await authFetch(`/agents/${encodeURIComponent(editingAgentId)}/train`, {
      method: "POST",
      body: data,
    });
    let body;
    try {
      body = await res.json();
    } catch (parseErr) {
      body = { detail: `${res.status} ${res.statusText}` };
    }
    agentTrainStatus.textContent = res.ok
      ? `Trained on ${file.name}.`
      : errorText(body, res);
    if (res.ok) {
      await loadAgentDocuments(editingAgentId);
      await loadAgents();
    }
  } catch (err) {
    agentTrainStatus.textContent = err.message;
  } finally {
    agentTrainFile.value = "";
  }
}

function resetAgentState() {
  agents = [];
  currentAgentId = null;
  editingAgentId = null;
  agentSelect.innerHTML = "";
  agentModal.hidden = true;
}
