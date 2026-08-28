// The owner's view of what visitors have been saying to a shared chatbot.
//
// Read-only by design: this reads two endpoints and writes nothing. The list
// comes from /chatbots/{id}/inbox; a transcript comes from the SAME
// /sessions/{id}/messages the owner's own chats use, which works because a
// visitor's session is created carrying the owner's own owner_id.
//
// One thing worth knowing while reading this: what the owner sees here is not
// what the visitor saw. Redaction happens on the public response, so the
// stored rows keep real filenames — the visitor read "Source 2" where this
// panel shows Q3.pdf. That asymmetry is deliberate: it is the owner's
// document.

const inboxModal = document.getElementById("inbox-modal");
const inboxList = document.getElementById("inbox-list");
const inboxReader = document.getElementById("inbox-reader");

let inboxBot = null;

async function openInbox(bot) {
  inboxBot = bot;
  // Blank both panes before the modal is visible, for the same reason
  // openShare does: a panel that opens showing the PREVIOUS chatbot's
  // conversations is worse than one that opens empty.
  inboxList.textContent = "";
  inboxReader.textContent = "";
  setInboxNote(inboxList, "Loading…");
  inboxModal.hidden = false;
  await loadInbox();
}

function setInboxNote(pane, text) {
  pane.textContent = "";
  const p = document.createElement("p");
  p.className = "inbox-note";
  p.textContent = text;
  pane.appendChild(p);
}

async function loadInbox() {
  const res = await authFetch(`/chatbots/${encodeURIComponent(inboxBot.id)}/inbox`);
  if (!res.ok) {
    setInboxNote(inboxList, "Could not load the inbox.");
    return;
  }
  paintInboxList(await res.json());
}

function paintInboxList(rows) {
  inboxList.textContent = "";
  if (rows.length === 0) {
    setInboxNote(inboxList, "No one has used your share link yet.");
    setInboxNote(inboxReader, "");
    return;
  }
  setInboxNote(inboxReader, "Pick a conversation to read it.");

  rows.forEach((row) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "inbox-item";
    item.dataset.sessionId = row.id;

    const preview = document.createElement("span");
    preview.className = "inbox-item__preview";
    // A session with no messages is a visitor who opened the widget and left.
    // It is a real row, so it gets said rather than shown as a blank line.
    preview.textContent = row.preview || "Opened, but never typed";
    if (!row.preview) preview.classList.add("inbox-item__preview--empty");

    const meta = document.createElement("span");
    meta.className = "inbox-item__meta";
    meta.textContent = `${row.message_count} ${
      row.message_count === 1 ? "message" : "messages"
    } · ${relativeTime(row.last_message_at)}`;

    item.append(preview, meta);
    item.addEventListener("click", () => selectConversation(item, row.id));
    inboxList.appendChild(item);
  });
}

function selectConversation(item, sessionId) {
  inboxList.querySelectorAll(".inbox-item").forEach((el) => {
    el.classList.toggle("inbox-item--active", el === item);
  });
  openConversation(sessionId);
}

async function openConversation(sessionId) {
  setInboxNote(inboxReader, "Loading…");
  const res = await authFetch(`/sessions/${encodeURIComponent(sessionId)}/messages`);
  if (!res.ok) {
    setInboxNote(inboxReader, "Could not load that conversation.");
    return;
  }
  const body = await res.json();
  paintConversation(body.messages || []);
}

function paintConversation(messages) {
  inboxReader.textContent = "";
  if (messages.length === 0) {
    setInboxNote(inboxReader, "This visitor opened the chat but never typed.");
    return;
  }
  messages.forEach((m) => inboxReader.appendChild(conversationRow(m)));
  inboxReader.scrollTop = 0;
}

function conversationRow(m) {
  const row = document.createElement("div");
  row.className = `row row--${m.role}`;

  if (m.role === "user") {
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const p = document.createElement("p");
    // Plain text, never markup: a visitor on someone else's website typed
    // this, and it is being displayed inside the owner's authenticated app.
    p.textContent = m.text;
    bubble.appendChild(p);
    row.appendChild(bubble);
    return row;
  }

  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.textContent = "AI";
  const content = document.createElement("div");
  content.className = "content";
  renderMarkdown(content, m.text);
  if (m.citations && m.citations.length > 0) {
    content.appendChild(buildReferenceList(m.citations));
  }
  if (m.answered_by && m.answered_by.name) {
    const badge = document.createElement("span");
    badge.className =
      "agent-badge" + (m.answered_by.id ? "" : " agent-badge--general");
    badge.textContent = m.answered_by.name;
    content.appendChild(badge);
  }
  row.append(avatar, content);
  return row;
}

function relativeTime(iso) {
  if (!iso) return "unknown";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "unknown";
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function wireInbox() {
  document.getElementById("inbox-close").addEventListener("click", () => {
    inboxModal.hidden = true;
  });
}
