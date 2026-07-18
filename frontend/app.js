const uploadButton = document.getElementById("upload-button");
const fileInput = document.getElementById("file-input");
const uploadStatus = document.getElementById("upload-status");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const messages = document.getElementById("messages");

let sessionId = null;

uploadButton.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) {
    setStatus("Choose a PDF first.", "error");
    return;
  }
  setStatus("Uploading and indexing…", null);
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
      setStatus(`${body.status} · ${body.source_id}`, body.status === "indexed" ? "ok" : null);
      // A newly uploaded document should get a fresh conversation — otherwise
      // the agent keeps its prior chat session, and multi-turn history can
      // anchor it on an earlier document instead of the new one.
      if (sessionId !== null) {
        sessionId = null;
        appendMessage("system", "System", "New document uploaded — starting a fresh conversation.");
      }
    } else {
      setStatus(body.detail || response.statusText, "error");
    }
  } catch (err) {
    setStatus(err.message, "error");
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = chatInput.value.trim();
  if (!query) return;
  appendMessage("user", "You", query);
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
      appendMessage("assistant", "Assistant", body.answer, body.citations);
    } else {
      appendMessage("error", "Error", body.detail || response.statusText);
    }
  } catch (err) {
    appendMessage("error", "Error", err.message);
  }
});

function setStatus(text, state) {
  uploadStatus.textContent = text;
  if (state) {
    uploadStatus.dataset.state = state;
  } else {
    delete uploadStatus.dataset.state;
  }
}

function appendMessage(role, who, text, citations) {
  const wrapper = document.createElement("div");
  wrapper.className = `msg msg--${role}`;

  const label = document.createElement("span");
  label.className = "msg__who";
  label.textContent = who;
  wrapper.appendChild(label);

  const body = document.createElement("p");
  body.className = "msg__body";
  body.textContent = text;
  wrapper.appendChild(body);

  if (citations && citations.length > 0) {
    wrapper.appendChild(buildReferenceList(citations));
  }

  messages.appendChild(wrapper);
  messages.scrollTop = messages.scrollHeight;
}

function buildReferenceList(citations) {
  const list = document.createElement("ul");
  list.className = "refs";
  citations.forEach((citation, index) => {
    const item = document.createElement("li");

    const tag = document.createElement("span");
    tag.className = "ref__tag";
    tag.textContent = `[${citation.key || index + 1}] `;
    item.appendChild(tag);

    const name = citation.source_name || citation.source_id || "source";
    const excerpt = citation.text_excerpt ? ` — "${truncate(citation.text_excerpt, 140)}"` : "";
    item.appendChild(document.createTextNode(`${name}${excerpt}`));

    list.appendChild(item);
  });
  return list;
}

function truncate(text, maxLength) {
  const clean = text.trim().replace(/\s+/g, " ");
  return clean.length > maxLength ? `${clean.slice(0, maxLength)}…` : clean;
}
