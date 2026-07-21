const profileInput = document.getElementById("profile-input");
const profileStatus = document.getElementById("profile-status");
const attachButton = document.getElementById("attach-button");
const fileInput = document.getElementById("file-input");
const attachmentChip = document.getElementById("attachment-chip");
const attachmentName = document.getElementById("attachment-name");
const attachmentStatus = document.getElementById("attachment-status");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendButton = document.getElementById("send-button");
const messages = document.getElementById("messages");

const PROFILE_KEY = "rag-chat-profile";
let sessionId = null;
let currentProfile = null;
let isAsking = false;

init();

function init() {
  setComposerEnabled(false);
  const saved = localStorage.getItem(PROFILE_KEY);
  if (saved) {
    profileInput.value = saved;
    switchProfile(saved);
  } else {
    setProfileStatus("Enter a profile name to start", null);
  }
  profileInput.addEventListener("change", () => switchProfile(profileInput.value));
  profileInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      switchProfile(profileInput.value);
    }
  });
}

async function switchProfile(rawName) {
  const name = rawName.trim();
  if (!name) {
    setProfileStatus("Enter a profile name to start", null);
    return;
  }
  // Leaving the old profile: clear its conversation and any attachment.
  clearThread();
  sessionId = null;
  attachmentChip.hidden = true;
  setComposerEnabled(false);
  setProfileStatus(`Setting up ${name}…`, null);

  try {
    const response = await fetch("/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: name }),
    });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok) {
      currentProfile = name;
      localStorage.setItem(PROFILE_KEY, name);
      setProfileStatus(`Profile: ${name}`, "ok");
      setComposerEnabled(true);
    } else {
      currentProfile = null;
      setProfileStatus(body.detail || response.statusText, "error");
    }
  } catch (err) {
    currentProfile = null;
    setProfileStatus(err.message, "error");
  }
}

attachButton.addEventListener("click", () => {
  fileInput.click();
});

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  if (!currentProfile) {
    setProfileStatus("Enter a profile name first", "error");
    fileInput.value = "";
    return;
  }

  showAttachment(file.name, "Uploading and indexing…", null);
  const formData = new FormData();
  formData.append("file", file);
  formData.append("profile", currentProfile);
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
  if (isAsking) return;
  const query = chatInput.value.trim();
  if (!query) return;
  if (!currentProfile) {
    appendMessage("error", "!", "Enter a profile name first.");
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
      body: JSON.stringify({ query, profile: currentProfile, session_id: sessionId }),
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

function setProfileStatus(text, state) {
  profileStatus.textContent = text;
  if (state) {
    profileStatus.dataset.state = state;
  } else {
    delete profileStatus.dataset.state;
  }
}

function clearThread() {
  messages.innerHTML = "";
  const note = document.createElement("div");
  note.className = "empty-state";
  note.textContent = "Upload a PDF, then ask anything about it.";
  messages.appendChild(note);
}

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

function appendThinking() {
  const existingEmpty = messages.querySelector(".empty-state");
  if (existingEmpty) {
    existingEmpty.remove();
  }

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
  for (let i = 0; i < 3; i++) {
    thinking.appendChild(document.createElement("span"));
  }
  content.appendChild(thinking);
  row.appendChild(content);

  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

function appendMessage(role, avatarText, text, citations) {
  const existingEmpty = messages.querySelector(".empty-state");
  if (existingEmpty) {
    existingEmpty.remove();
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
