// The current chatbot's knowledge base: trained once, searched by every
// agent it owns. Kept out of agents.js, which already carries the agent
// list, form, training and deletion.
//
// Training is accepted immediately and finishes in the background, so this
// polls for status — the same shape as agent training, because a large PDF
// takes minutes to extract and a blocking request cannot wait that long.

const knowledgeModal = document.getElementById("knowledge-modal");
const knowledgeDocList = document.getElementById("knowledge-doc-list");
const knowledgeFile = document.getElementById("knowledge-file");
const knowledgeStatus = document.getElementById("knowledge-status");

const KNOWLEDGE_POLL_MS = 3000;
const KNOWLEDGE_POLL_LIMIT = 200; // ~10 minutes, matching the server's budget

let knowledgeTraining = false;

function wireKnowledge() {
  document.getElementById("my-knowledge").addEventListener("click", openKnowledge);
  document.getElementById("knowledge-close").addEventListener("click", () => {
    knowledgeModal.hidden = true;
  });
  knowledgeFile.addEventListener("change", trainKnowledge);
  knowledgeModal.addEventListener("keydown", (event) => {
    if (event.key === "Escape") knowledgeModal.hidden = true;
  });
}

function openKnowledge() {
  knowledgeModal.hidden = false;
  knowledgeStatus.textContent = "";
  loadKnowledgeDocuments();
}

async function loadKnowledgeDocuments() {
  knowledgeDocList.innerHTML = "";
  let docs;
  try {
    const res = await authFetch(
      `/knowledge/documents?chatbot_id=${encodeURIComponent(currentChatbotId)}`
    );
    if (!res.ok) return;
    docs = await res.json();
  } catch (err) {
    knowledgeStatus.textContent = err.message;
    return;
  }
  if (docs.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "Nothing yet. Anything you add here is available to every agent in this chatbot.";
    knowledgeDocList.appendChild(li);
    return;
  }
  docs.forEach((doc) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = doc.filename || doc.source_id;
    li.appendChild(name);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => untrainKnowledge(doc.source_id));
    li.appendChild(remove);
    knowledgeDocList.appendChild(li);
  });
}

async function untrainKnowledge(sourceId) {
  try {
    const res = await authFetch(
      `/knowledge/documents/${encodeURIComponent(sourceId)}` +
      `?chatbot_id=${encodeURIComponent(currentChatbotId)}`,
      { method: "DELETE" }
    );
    if (!res.ok) {
      let body;
      try {
        body = await res.json();
      } catch (parseErr) {
        body = { detail: `${res.status} ${res.statusText}` };
      }
      knowledgeStatus.textContent = errorText(body, res);
      return;
    }
    await loadKnowledgeDocuments();
  } catch (err) {
    knowledgeStatus.textContent = err.message;
  }
}

async function pollKnowledgeStatus(sourceId, filename) {
  for (let i = 0; i < KNOWLEDGE_POLL_LIMIT; i += 1) {
    await new Promise((r) => setTimeout(r, KNOWLEDGE_POLL_MS));
    const res = await authFetch(
      `/knowledge/documents/${encodeURIComponent(sourceId)}/status` +
      `?chatbot_id=${encodeURIComponent(currentChatbotId)}`
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

async function trainKnowledge() {
  const file = knowledgeFile.files[0];
  if (!file || knowledgeTraining) return;
  knowledgeTraining = true;
  knowledgeFile.disabled = true;
  knowledgeStatus.textContent = `Reading ${file.name}… this can take a few minutes for a large document.`;
  const data = new FormData();
  data.append("file", file);
  data.append("chatbot_id", currentChatbotId);
  try {
    const res = await authFetch("/knowledge/train", { method: "POST", body: data });
    let body;
    try {
      body = await res.json();
    } catch (parseErr) {
      body = { detail: `${res.status} ${res.statusText}` };
    }
    if (!res.ok) {
      knowledgeStatus.textContent = errorText(body, res);
      return;
    }
    const result = await pollKnowledgeStatus(body.source_id, file.name);
    knowledgeStatus.textContent = result.ok
      ? `Added ${file.name}. Every agent in this chatbot can use it now.`
      : result.detail;
    if (result.ok) await loadKnowledgeDocuments();
  } catch (err) {
    knowledgeStatus.textContent = err.message;
  } finally {
    knowledgeTraining = false;
    knowledgeFile.disabled = false;
    knowledgeFile.value = "";
  }
}
