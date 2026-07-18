const attachButton = document.getElementById("attach-button");
const fileInput = document.getElementById("file-input");
const attachmentChip = document.getElementById("attachment-chip");
const attachmentName = document.getElementById("attachment-name");
const attachmentStatus = document.getElementById("attachment-status");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const messages = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");

let sessionId = null;

attachButton.addEventListener("click", () => {
  fileInput.click();
});

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;

  showAttachment(file.name, "Uploading and indexing…", null);
  const formData = new FormData();
  formData.append("file", file);
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
      // A newly uploaded document should get a fresh conversation — otherwise
      // the agent keeps its prior chat session, and multi-turn history can
      // anchor it on an earlier document instead of the new one.
      if (sessionId !== null) {
        sessionId = null;
        appendMessage("system", null, "New document uploaded — starting a fresh conversation.");
      }
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
  const query = chatInput.value.trim();
  if (!query) return;
  appendMessage("user", null, query);
  chatInput.value = "";

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, session_id: sessionId }),
    });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok) {
      sessionId = body.session_id;
      appendMessage("assistant", "AI", body.answer, body.citations);
    } else {
      appendMessage("error", "!", body.detail || response.statusText);
    }
  } catch (err) {
    appendMessage("error", "!", err.message);
  }
});

function showAttachment(name, statusText, state) {
  attachmentChip.hidden = false;
  attachmentName.textContent = name;
  attachmentStatus.textContent = statusText;
  if (state) {
    attachmentStatus.dataset.state = state;
  } else {
    delete attachmentStatus.dataset.state;
  }
}

function appendMessage(role, avatarText, text, citations) {
  if (emptyState && emptyState.parentNode) {
    emptyState.remove();
  }

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
    if (citation.text_excerpt) {
      item.title = citation.text_excerpt;
    }

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
