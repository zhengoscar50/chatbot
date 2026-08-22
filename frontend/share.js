// The public chat page. No accounts, no uploads, no navigation.
//
// The token comes from the path — /s/<token> — and is the only credential.
// Everything the page shows comes from three endpoints that return the
// chatbot's name and its answers, and nothing else about the owner.
//
// This page loads only markdown.js and share.js — never app.js, which carries
// account concepts (agents, sessions list, uploads) that have no business
// here. markdown.js exports `parseMarkdown` (tokens), not a renderer, so the
// small renderer below is a deliberate duplicate of app.js's renderMarkdown/
// appendSpans rather than a reason to load app.js.

const TOKEN = location.pathname.split("/")[2] || "";
const thread = document.getElementById("thread");
const input = document.getElementById("q");
// Not named `status`: `window.status` already exists as a global in browsers,
// and a top-level `const status = …` risks colliding with it.
const statusLine = document.getElementById("status");
let sessionId = null;
let busy = false;

async function api(path, body) {
  const res = await fetch(`/s/${encodeURIComponent(TOKEN)}${path}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  return res;
}

// Renders markdown tokens (from markdown.js's parseMarkdown) as elements.
// Nothing here builds an HTML string, so an answer — which summarises
// documents the visitor did not write — cannot introduce markup.
function appendSpans(parent, spans) {
  spans.forEach((span) => {
    if (span.type === "text") {
      parent.appendChild(document.createTextNode(span.text));
      return;
    }
    const tag = span.type === "strong" ? "strong" : span.type === "em" ? "em" : "code";
    const el = document.createElement(tag);
    el.textContent = span.text;
    parent.appendChild(el);
  });
}

function renderMarkdown(container, text) {
  const tokens = parseMarkdown(text);
  if (tokens.length === 0) {
    const p = document.createElement("p");
    p.textContent = text || "";
    container.appendChild(p);
    return;
  }
  tokens.forEach((token) => {
    if (token.type === "code") {
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = token.text;
      pre.appendChild(code);
      container.appendChild(pre);
      return;
    }
    if (token.type === "list") {
      const list = document.createElement(token.ordered ? "ol" : "ul");
      list.className = "md-list";
      token.items.forEach((spans) => {
        const li = document.createElement("li");
        appendSpans(li, spans);
        list.appendChild(li);
      });
      container.appendChild(list);
      return;
    }
    const el = document.createElement(
      token.type === "heading" ? `h${Math.min(token.level + 2, 6)}` : "p"
    );
    if (token.type === "heading") el.className = "md-heading";
    appendSpans(el, token.spans);
    container.appendChild(el);
  });
}

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

async function boot() {
  const res = await fetch(`/s/${encodeURIComponent(TOKEN)}/info`);
  if (!res.ok) {
    statusLine.textContent = "This link isn't available.";
    input.disabled = true;
    return;
  }
  const info = await res.json();
  document.getElementById("bot-name").textContent = info.name;
  document.title = info.name;
  if (info.description) {
    document.getElementById("bot-desc").textContent = info.description;
  }
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
