// Agent management: list, create, edit, train, delete.
//
// Kept out of app.js, which already carries auth, sessions, chat, uploads and
// rendering. Loaded before app.js; app.js calls wireAgents() and loadAgents(),
// and this file calls back into onAgentChanged()/authFetch()/errorText().

let agents = [];
let editingAgentId = null;

const agentModal = document.getElementById("agent-modal");
const agentForm = document.getElementById("agent-form");
const agentNameInput = document.getElementById("agent-name");
const agentInstructionsInput = document.getElementById("agent-instructions");
const agentModelInput = document.getElementById("agent-model");
const agentModelSelect = document.getElementById("agent-model-select");
const agentModelCustomRow = document.getElementById("agent-model-custom-row");

const OTHER_MODEL = "__other__";
let modelChoices = [];
let defaultModel = "";
// Per-model context ceilings, served by /models so the limits live in one
// place. The server clamps whatever arrives regardless — this only stops the
// form offering a value that would be silently reduced.
let contextLimits = {};
let contextDefault = 32000;
let contextMin = 1000;
// Served by /models for the same reason context limits are: the form must
// not keep its own copy of which models honour an effort setting.
let effortModels = [];
const agentGroundingInput = document.getElementById("agent-grounding");
const agentDocsSection = document.getElementById("agent-docs");
const agentDocList = document.getElementById("agent-doc-list");
const agentTrainFile = document.getElementById("agent-train-file");
const agentTrainStatus = document.getElementById("agent-train-status");
const agentError = document.getElementById("agent-error");
const agentDeleteButton = document.getElementById("agent-delete");
const agentModalTitle = document.getElementById("agent-modal-title");
const agentSaveButton = document.getElementById("agent-save");
const agentDescriptionInput = document.getElementById("agent-description");
const agentContextInput = document.getElementById("agent-context");
const agentEffortRow = document.getElementById("agent-effort-row");
const agentEffortSelect = document.getElementById("agent-effort");
const agentContextReadout = document.getElementById("agent-context-readout");
const agentListModal = document.getElementById("agent-list-modal");
const agentList = document.getElementById("agent-list");

function wireAgents() {
  document.getElementById("manage-agents").addEventListener("click", openAgentList);
  document.getElementById("agent-list-close").addEventListener("click", () => {
    agentListModal.hidden = true;
  });
  document.getElementById("agent-list-new").addEventListener("click", () => {
    agentListModal.hidden = true;
    openAgentModal(null);
  });
  document.getElementById("agent-cancel").addEventListener("click", closeAgentModal);
  agentForm.addEventListener("submit", saveAgent);
  agentDeleteButton.addEventListener("click", deleteAgent);
  agentTrainFile.addEventListener("change", trainAgent);
  agentModelSelect.addEventListener("change", () => {
    renderEffortRow(agentModelSelect.value);
    const custom = agentModelSelect.value === OTHER_MODEL;
    agentModelCustomRow.hidden = !custom;
    if (custom) agentModelInput.focus();
    // A smaller model means a lower ceiling; move the thumb down with it
    // rather than showing a value the server would quietly reduce.
    applyContextCeiling();
  });
  agentModelInput.addEventListener("input", applyContextCeiling);
  agentContextInput.addEventListener("input", renderContextReadout);
  agentModal.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAgentModal();
  });
}

async function loadAgents() {
  if (modelChoices.length === 0) await loadModelChoices();
  try {
    const res = await authFetch(`/agents?chatbot_id=${encodeURIComponent(currentChatbotId)}`);
    if (res.status === 404) {
      // The only reason this scoped query 404s is that the chatbot itself is
      // gone (e.g. deleted from another tab) — bounce back to the dashboard
      // instead of leaving the chat view stranded.
      await handleChatbotGone();
      return;
    }
    if (!res.ok) {
      // Say something. A silently blank picker leaves the user with no idea
      // whether they have no agents or the request failed.
      let body;
      try {
        body = await res.json();
      } catch (parseErr) {
        body = { detail: `${res.status} ${res.statusText}` };
      }
      agents = [];
      setSidebarStatus(`Could not load agents: ${errorText(body, res)}`, "error");
      return;
    }
    agents = await res.json();
  } catch (err) {
    agents = [];
    setSidebarStatus(err.message, "error");
    return;
  }
  if (!agentListModal.hidden) renderAgentList();
}

// Guards a button against double submission. Creating an agent takes a couple
// of seconds (it probes the model against the provider), and with nothing
// visibly happening an impatient user clicks again — which used to create one
// agent per click. Duplicates are not just clutter: identical descriptions
// give the orchestrator indistinguishable options to route between, and every
// one of them lengthens the routing prompt on every message.
async function withBusy(button, busyLabel, fn) {
  if (button.disabled) return;          // already in flight
  const original = button.textContent;
  button.disabled = true;
  if (busyLabel) button.textContent = busyLabel;
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}


function openAgentList() {
  agentListModal.hidden = false;
  renderAgentList();
}

function renderAgentList() {
  agentList.innerHTML = "";
  if (agents.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "No agents yet. Create one to give the orchestrator something to route to.";
    agentList.appendChild(li);
    return;
  }
  agents.forEach((a) => {
    const li = document.createElement("li");
    const main = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = a.name;
    main.appendChild(name);
    const desc = document.createElement("div");
    desc.className = "muted";
    // An agent with no description is effectively unroutable — say so.
    desc.textContent = a.description
      || "No description — the orchestrator can't route to this reliably.";
    main.appendChild(desc);
    li.appendChild(main);
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => {
      agentListModal.hidden = true;
      openAgentModal(a.id);
    });
    li.appendChild(edit);
    agentList.appendChild(li);
  });
}

// The model list is hand-maintained server-side (Powabase publishes no
// catalog), so "Other…" stays available for ids the list doesn't know yet.
async function loadModelChoices() {
  try {
    const res = await authFetch("/models");
    if (!res.ok) return;
    const body = await res.json();
    modelChoices = body.models || [];
    defaultModel = body.default || "";
    contextLimits = body.context_limits || {};
    contextDefault = body.context_default || contextDefault;
    contextMin = body.context_min || contextMin;
    effortModels = body.effort_models || [];
  } catch (err) {
    modelChoices = [];
  }
}

function renderModelSelect(current) {
  agentModelSelect.innerHTML = "";
  modelChoices.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m === defaultModel ? `${m} (default)` : m;
    agentModelSelect.appendChild(opt);
  });
  const other = document.createElement("option");
  other.value = OTHER_MODEL;
  other.textContent = "Other…";
  agentModelSelect.appendChild(other);

  // An agent may already hold a model the list doesn't offer — one typed
  // before the picker existed, or dropped from the list since. Show it as a
  // custom value rather than silently re-pointing the agent at another model.
  if (current && !modelChoices.includes(current)) {
    agentModelSelect.value = OTHER_MODEL;
    agentModelInput.value = current;
    agentModelCustomRow.hidden = false;
  } else {
    agentModelSelect.value = current || defaultModel || (modelChoices[0] || OTHER_MODEL);
    agentModelInput.value = "";
    agentModelCustomRow.hidden = true;
  }
}

function chosenModel() {
  return agentModelSelect.value === OTHER_MODEL
    ? agentModelInput.value.trim()
    : agentModelSelect.value;
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
    agentDescriptionInput.value = "";
    agentInstructionsInput.value = "";
    agentGroundingInput.value = "strict";
    renderModelSelect(null);
    applyContextCeiling(contextDefault);
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
  agentDescriptionInput.value = a.description || "";
  agentInstructionsInput.value = a.instructions || "";
  agentGroundingInput.value = a.grounding;
  renderModelSelect(a.model || "");
  // The server sends the stored value already clamped to this model's ceiling.
  applyContextCeiling(a.max_context_tokens);
  renderEffortRow(a.model || "");
  agentEffortSelect.value = a.reasoning_effort || "";
}

async function saveAgent(event) {
  event.preventDefault();
  return withBusy(agentSaveButton, "Saving…", () => saveAgentRequest());
}

async function saveAgentRequest() {
  agentError.textContent = "";
  const payload = {
    name: agentNameInput.value.trim(),
    description: agentDescriptionInput.value.trim(),
    instructions: agentInstructionsInput.value,
    grounding: agentGroundingInput.value,
    max_context_tokens: Number(agentContextInput.value),
    // "" is the Default level. Sent as null so the server can tell an explicit
    // reset apart from a field the form never touched.
    reasoning_effort: agentEffortSelect.value || null,
  };
  const model = chosenModel();
  if (agentModelSelect.value === OTHER_MODEL && !model) {
    agentError.textContent = "Enter a model id, or pick one from the list.";
    return;
  }
  if (model) payload.model = model;
  // Only creation needs the chatbot id — an existing agent already belongs to
  // one, and PATCH has no field for it.
  if (!editingAgentId) payload.chatbot_id = currentChatbotId;

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
    closeAgentModal();
    await loadAgents();
    openAgentList();
  } catch (err) {
    agentError.textContent = err.message;
  }
}

async function loadAgentDocuments(agentId) {
  agentDocList.innerHTML = "";
  if (!agentId) return;   // the dialog closed while a request was in flight
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

let training = false;

// Training is accepted immediately and finishes in the background, so the UI
// polls. A large PDF takes minutes to extract; the old blocking request timed
// out long before that and reported a failure for work that was fine.
const TRAIN_POLL_MS = 3000;
const TRAIN_POLL_LIMIT = 200; // ~10 minutes, matching the server's budget

async function pollTrainingStatus(agentId, sourceId, filename) {
  for (let i = 0; i < TRAIN_POLL_LIMIT; i += 1) {
    await new Promise((r) => setTimeout(r, TRAIN_POLL_MS));
    const res = await authFetch(
      `/agents/${encodeURIComponent(agentId)}/documents/${encodeURIComponent(sourceId)}/status`
    );
    if (!res.ok) continue; // a blip mid-poll is not a failed upload
    const body = await res.json();
    if (body.status === "indexed") return { ok: true };
    if (body.status === "failed") {
      return { ok: false, detail: body.detail || `Could not read ${filename}.` };
    }
  }
  return { ok: false, detail: `${filename} is taking longer than expected.` };
}

async function trainAgent() {
  const file = agentTrainFile.files[0];
  // Capture the id now: the dialog may be closed while this runs, which sets
  // editingAgentId back to null and made the refresh below request
  // /agents/null/documents.
  const agentId = editingAgentId;
  if (!file || !agentId || training) return;
  training = true;
  agentTrainFile.disabled = true;
  agentTrainStatus.textContent = `Reading ${file.name}… this can take a few minutes for a large document.`;
  const data = new FormData();
  data.append("file", file);
  try {
    const res = await authFetch(`/agents/${encodeURIComponent(agentId)}/train`, {
      method: "POST",
      body: data,
    });
    let body;
    try {
      body = await res.json();
    } catch (parseErr) {
      body = { detail: `${res.status} ${res.statusText}` };
    }
    if (!res.ok) {
      agentTrainStatus.textContent = errorText(body, res);
      return;
    }
    const result = await pollTrainingStatus(agentId, body.source_id, file.name);
    agentTrainStatus.textContent = result.ok
      ? `Trained on ${file.name}.`
      : result.detail;
    if (result.ok) {
      await loadAgentDocuments(agentId);
      await loadAgents();
    }
  } catch (err) {
    agentTrainStatus.textContent = err.message;
  } finally {
    training = false;
    agentTrainFile.disabled = false;
    agentTrainFile.value = "";
  }
}

function resetAgentState() {
  agents = [];
  modelChoices = [];
  editingAgentId = null;
  agentModal.hidden = true;
  agentListModal.hidden = true;
}


// --- context budget ---------------------------------------------------------

function ceilingFor(model) {
  const known = contextLimits[model];
  if (known && known.max) return known.max;
  // An id the table has never seen — the "Other…" hatch makes that expected.
  // Mirror the server's conservative guess rather than promising more.
  return 16000;
}

function renderContextReadout() {
  const value = Number(agentContextInput.value);
  const max = Number(agentContextInput.max);
  agentContextReadout.textContent =
    `${value.toLocaleString()} tokens of retrieved text — up to ${max.toLocaleString()} for this model`;
}

function applyContextCeiling(preferred) {
  const max = ceilingFor(chosenModel());
  agentContextInput.min = contextMin;
  agentContextInput.max = max;
  const wanted = Number(preferred != null ? preferred : agentContextInput.value) || contextDefault;
  agentContextInput.value = Math.max(contextMin, Math.min(wanted, max));
  renderContextReadout();
}


// Shown only for models measured to honour it. A control that saves a value
// the provider ignores is worse than no control: Powabase stores any setting
// without validating it, so it would look saved and do nothing.
function renderEffortRow(model) {
  const supported = effortModels.includes(model);
  agentEffortRow.hidden = !supported;
  if (!supported) agentEffortSelect.value = "";
}
