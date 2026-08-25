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

function citations(list) {
  if (!list || !list.length) return;
  const box = document.createElement("div");
  box.className = "citations";
  list.forEach((c) => {
    const item = document.createElement("p");
    item.className = "citation";
    // source_name is already "Source 1" — the server redacts filenames.
    item.textContent = `[${c.key}] ${c.source_name}: ${c.text_excerpt}`;
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
    const close = document.getElementById("embed-close");
    close.hidden = false;
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
