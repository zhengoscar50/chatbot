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
    uploadStatus.textContent = "Choose a PDF first.";
    return;
  }
  uploadStatus.textContent = "Uploading and indexing...";
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
      uploadStatus.textContent = `Status: ${body.status} (source ${body.source_id})`;
      // A newly uploaded document should get a fresh conversation — otherwise
      // the agent keeps its prior chat session, and multi-turn history can
      // anchor it on an earlier document instead of the new one.
      if (sessionId !== null) {
        sessionId = null;
        appendMessage("System", "New document uploaded — starting a fresh conversation.");
      }
    } else {
      uploadStatus.textContent = `Error: ${body.detail || response.statusText}`;
    }
  } catch (err) {
    uploadStatus.textContent = `Error: ${err.message}`;
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = chatInput.value.trim();
  if (!query) return;
  appendMessage("You", query);
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
      appendMessage("Assistant", body.answer);
    } else {
      appendMessage("Error", body.detail || response.statusText);
    }
  } catch (err) {
    appendMessage("Error", err.message);
  }
});

function appendMessage(who, text) {
  const el = document.createElement("p");
  el.innerHTML = `<strong>${who}:</strong> ${escapeHtml(text)}`;
  messages.appendChild(el);
  messages.scrollTop = messages.scrollHeight;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
