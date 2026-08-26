// The public chat page. No accounts, no uploads, no navigation.
//
// The token comes from the path — /s/<token> — and is the only credential.
// Everything the page shows comes from three endpoints that return the
// chatbot's name and its answers, and nothing else about the owner.
//
// This page loads only markdown.js and share.js — never app.js, which carries
// account concepts (agents, sessions list, uploads) that have no business
// here. markdown.js exports both the parser and the renderMarkdown/
// appendSpans renderer, shared with app.js, so a markdown fix lands on this
// public page the same moment it lands on the account one.

const TOKEN = location.pathname.split("/")[2] || "";

// Embed mode: this page is inside the widget's iframe on somebody else's site.
// The loader owns the session and hands it over in the fragment, so the page
// resumes rather than starting a second conversation beside the first.
const EMBEDDED = new URLSearchParams(location.search).get("embed") === "1";
const HANDED_SESSION = new URLSearchParams(
  location.hash.replace(/^#/, "")
).get("session");

const thread = document.getElementById("thread");
const input = document.getElementById("q");
// Not named `status`: `window.status` already exists as a global in browsers,
// and a top-level `const status = …` risks colliding with it.
const statusLine = document.getElementById("status");
let sessionId = HANDED_SESSION || null;
let busy = false;

async function api(path, body) {
  const res = await fetch(`/s/${encodeURIComponent(TOKEN)}${path}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  return res;
}

// renderMarkdown/appendSpans live in markdown.js, loaded above.

function bubble(role, text) {
  const el = document.createElement("div");
  el.className = `bubble bubble--${role}`;
  if (role === "assistant") {
    renderMarkdown(el, text);
  } else {
    el.textContent = text; // never innerHTML: this is the visitor's own input
  }
  thread.appendChild(el);
  thread.scrollTop = thread.scrollHeight;
  return el;
}

// An excerpt is a raw chunk of the source document: up to 300 characters, with
// newlines, and usually opening on a markdown heading like "# EMPLOYEE
// HANDBOOK". Dropped into a paragraph as-is that renders as literal hash marks
// and a run-on wall of text — in the 360px widget card it buries the answer it
// is supposed to support.
//
// This is a quote, not a document, so the markdown is noise rather than
// meaning: flatten it to one line of plain prose and let CSS decide how much
// fits. Deliberately not the markdown renderer — headings and lists inside a
// citation chip would fight the layout rather than help it.
function excerptText(raw) {
  return String(raw || "")
    .replace(/^\s*#{1,6}\s+/gm, "")     // heading markers, any line
    .replace(/^\s*[-*+]\s+/gm, "")      // list bullets
    .replace(/[*_`]/g, "")               // emphasis and code ticks
    .replace(/\s+/g, " ")                // newlines and runs of space
    .trim();
}

function citations(list) {
  if (!list || !list.length) return;
  const box = document.createElement("div");
  box.className = "citations";
  list.forEach((c) => {
    const item = document.createElement("p");
    item.className = "citation";
    const marker = document.createElement("span");
    marker.className = "citation__marker";
    // source_name is already "Source 1" — the server redacts filenames.
    marker.textContent = `[${c.key}] ${c.source_name}`;
    item.appendChild(marker);
    item.appendChild(document.createTextNode(" " + excerptText(c.text_excerpt)));
    box.appendChild(item);
  });
  thread.appendChild(box);
}

// Replay what the visitor already said, so navigating the host site does not
// throw their conversation away.
//
// A 404 here is not expected: the loader validates the id before handing it
// over, precisely so this page can trust it. Treat it as nothing to replay
// rather than as an error to show, and do NOT clear sessionId — clearing it
// would send the page down its own create-a-session path and put two owners
// on the same conversation.
//
// A turn is rendered with two calls, not one: bubble() puts the text on
// screen, citations() (when present) follows it. The agent name that
// answered is deliberately dropped here — it belongs on the transient
// statusLine for the turn just taken, and replaying it per message would
// leave a stale agent name displayed as if it were current.
async function replay() {
  if (!sessionId) return;
  const res = await fetch(
    `/s/${encodeURIComponent(TOKEN)}/session/${encodeURIComponent(sessionId)}/messages`
  );
  if (!res.ok) return;
  const body = await res.json();
  (body.messages || []).forEach((m) => {
    bubble(m.role, m.content);
    if (m.role === "assistant" && (m.citations || []).length) {
      citations(m.citations);
    }
  });
}

// Attaching a PDF to this conversation only. It goes to the same per-session
// scratch store the account app uses, so it is readable here and nowhere else.
//
// There is deliberately no "save to chatbot knowledge" control, which the
// account app does offer: that writes into the owner's permanent knowledge for
// every future conversation, and a stranger on somebody else's website has no
// business doing it.
function wireAttach() {
  const button = document.getElementById("share-attach");
  const picker = document.getElementById("share-file");
  const chip = document.getElementById("share-chip");
  const name = document.getElementById("share-chip-name");
  const state = document.getElementById("share-chip-status");

  button.addEventListener("click", () => picker.click());

  picker.addEventListener("change", async () => {
    const file = picker.files && picker.files[0];
    if (!file || !sessionId) return;
    picker.value = "";               // so the same file can be picked again
    chip.hidden = false;
    name.textContent = file.name;
    state.textContent = "uploading…";

    const form = new FormData();
    form.append("session_id", sessionId);
    form.append("file", file);
    try {
      const res = await fetch(`/s/${encodeURIComponent(TOKEN)}/upload`, {
        method: "POST", body: form,
      });
      if (res.status === 429) {
        const body = await res.json().catch(() => ({}));
        state.textContent = body.detail || "Daily limit reached.";
        return;
      }
      // 202: extraction and indexing continue after the response. Saying
      // "ready" here would be a lie the visitor could act on by asking about a
      // document that is not searchable yet.
      state.textContent = res.ok ? "added to this chat" : "couldn't add that";
    } catch (err) {
      state.textContent = "couldn't add that";
    }
  });
}

async function boot() {
  // Held busy for the whole boot: send() (the button and Enter alike) bails
  // out on `busy`, so nothing can reach its own create-a-session branch
  // before /info resolves, or ever, on either "unavailable" path below.
  busy = true;
  const res = await fetch(`/s/${encodeURIComponent(TOKEN)}/info`);
  if (!res.ok) {
    statusLine.textContent = "This link isn't available.";
    input.disabled = true;
    document.getElementById("send").disabled = true;
    return; // busy stays true: there is nothing here to send to.
  }
  const info = await res.json();
  document.getElementById("bot-name").textContent = info.name;
  document.title = info.name;
  if (info.description) {
    document.getElementById("bot-desc").textContent = info.description;
  }

  if (EMBEDDED) {
    document.getElementById("embed-actions").hidden = false;
    document.getElementById("share-attach").hidden = false;
    wireAttach();

    document.getElementById("embed-reset").addEventListener("click", () => {
      // The loader owns the session, so it has to be the one to throw it away.
      // Clearing it here would leave the loader still holding the old id and
      // this page quietly making a new one — two owners of one conversation,
      // which is the split this whole design exists to avoid.
      parent.postMessage({ source: "powabase-widget", type: "reset" }, "*");
    });

    const close = document.getElementById("embed-close");
    close.addEventListener("click", () => {
      // The only two messages that cross the frame boundary are this and
      // "ready". No session id is ever posted out: the parent's origin cannot
      // be verified from in here, so nothing worth stealing is sent.
      parent.postMessage({ source: "powabase-widget", type: "close" }, "*");
    });
    parent.postMessage({ source: "powabase-widget", type: "ready" }, "*");
    if (!sessionId) {
      // The loader can fail to get a session and still load this page, so the
      // visitor sees something rather than a blank rectangle. With no session
      // to resume, don't let send() quietly create one of its own here —
      // that would happen outside the loader's bookkeeping.
      statusLine.textContent = "Chat is unavailable right now.";
      input.disabled = true;
      document.getElementById("send").disabled = true;
      return; // busy stays true forever: every route into send() is closed.
    }
  }
  await replay();
  busy = false;
}

async function send() {
  const text = input.value.trim();
  if (!text || busy) return;
  busy = true;
  input.value = "";
  bubble("user", text);
  statusLine.textContent = "Thinking…";
  try {
    // Lazy on purpose: a bot that only opens the link never creates a
    // session row, because nothing here runs until a visitor sends a message.
    if (!sessionId) {
      const created = await api("/session");
      if (!created.ok) throw new Error("unavailable");
      sessionId = (await created.json()).session_id;
    }
    const res = await api("/chat", { session_id: sessionId, query: text });
    const body = await res.json().catch(() => ({}));
    if (res.status === 429) {
      // The server's sentence, shown as-is — it is written for the visitor.
      statusLine.textContent = body.detail || "This demo has reached its limit for today.";
      return;
    }
    if (!res.ok) {
      statusLine.textContent = "Sorry — that didn't work. Try again.";
      return;
    }
    statusLine.textContent = body.answered_by ? `answered by ${body.answered_by.name}` : "";
    bubble("assistant", body.answer);
    citations(body.citations);
  } catch (err) {
    statusLine.textContent = "Sorry — that didn't work. Try again.";
  } finally {
    busy = false;
  }
}

document.getElementById("send").addEventListener("click", send);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
boot();
