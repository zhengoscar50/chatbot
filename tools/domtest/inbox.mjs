// The owner's inbox: the list of visitor conversations, and reading one.
//
// What this is really guarding is that the panel shows the OWNER's view of a
// stranger's conversation without turning that stranger's text into markup.
// The list itself is server-filtered, so the interesting frontend questions
// are: does it ask the right endpoint, does it survive the rows the server
// really returns (including a session nobody typed in), and is a visitor's
// message escaped.

import { JSDOM, VirtualConsole } from "jsdom";
import { readFileSync } from "fs";

// Overridden by run-all.sh so the harness is not tied to one machine.
const FE = process.env.RAGCHAT_FE || "/Users/oscar/Downloads/rag-chatbot/frontend";
const results = [];
const check = (ok, label, detail = "") => {
  results.push({ ok, label });
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
};
const flush = async (n = 25) => { for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0)); };

function makeServer(state) {
  return async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    const [path] = String(url).split("?");
    const json = (status, body) => ({ ok: status >= 200 && status < 300, status, statusText: "", json: async () => body });

    if (path === "/auth/signup-policy") return json(200, { invite_required: false });
    if (path === "/chatbots" && method === "GET") return json(200, state.chatbots);
    if (path === "/agents") return json(200, []);
    if (path === "/sessions") return json(200, []);
    if (path === "/knowledge/documents") return json(200, []);
    if (path === "/onboarding") return json(200, { steps: [], complete: true });

    const inbox = path.match(/^\/chatbots\/([^/]+)\/inbox$/);
    if (inbox) {
      state.inboxCalls.push(inbox[1]);
      if (state.slowMs) await new Promise((r) => setTimeout(r, state.slowMs));
      if (state.inboxStatus !== 200) return json(state.inboxStatus, { detail: "nope" });
      return json(200, state.inboxRows);
    }
    const msgs = path.match(/^\/sessions\/([^/]+)\/messages$/);
    if (msgs) {
      state.messageCalls.push(msgs[1]);
      return json(200, { messages: state.transcripts[msgs[1]] || [] });
    }
    return json(404, { detail: "not found" });
  };
}

function baseState() {
  return {
    chatbots: [{ id: "cb-a", name: "Alpha", description: "", share_token: "tok-a" }],
    inboxRows: [],
    transcripts: {},
    inboxCalls: [],
    messageCalls: [],
    inboxStatus: 200,
    slowMs: 0,
  };
}

async function boot(state) {
  const vc = new VirtualConsole();
  const errors = [];
  vc.on("jsdomError", (e) => errors.push(String(e.message || e)));

  let html = readFileSync(`${FE}/index.html`, "utf8").replace(/<script src="[^"]*"><\/script>/g, "");
  html = html.replace("</head>", `<style>${readFileSync(`${FE}/styles.css`, "utf8")}</style></head>`);
  const dom = new JSDOM(html, { runScripts: "dangerously", url: "https://host/", virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window, d = w.document;
  w.matchMedia = (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false });

  w.fetch = makeServer(state);
  w.localStorage.setItem("rag-chat-token", "t");
  w.localStorage.setItem("rag-chat-username", "oscar");
  w.confirm = () => true;
  w.prompt = () => null;

  for (const f of ["theme.js", "markdown.js", "agents.js", "knowledge.js", "scope.js", "chatbots.js", "onboarding.js", "tour-spotlight.js", "tour-steps.js", "tour.js", "inbox.js", "dashboard.js", "app.js"]) {
    const el = d.createElement("script");
    el.textContent = readFileSync(`${FE}/${f}`, "utf8");
    d.body.appendChild(el);
  }
  await flush(40);
  return { w, d, state, errors };
}

const $ = (d, s) => d.querySelector(s);
const $$ = (d, s) => Array.from(d.querySelectorAll(s));
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

async function openInboxPanel(w, d) {
  const card = $$(d, ".bot-card:not(.bot-card--new)").find((c) => c.textContent.includes("Alpha"));
  click(w, card.querySelector(".bot-card__menu"));
  await flush(3);
  const item = $$(card, ".bot-card__actions button").find((b) => b.textContent === "Inbox");
  click(w, item);
  await flush(10);
  return item;
}

console.log("\n=== opening the inbox ===");
{
  const state = baseState();
  state.inboxRows = [
    { id: "v1", preview: "do you ship to canada", message_count: 4, last_message_at: new Date(Date.now() - 3600e3).toISOString() },
    { id: "v2", preview: "", message_count: 0, last_message_at: new Date(Date.now() - 60e3).toISOString() },
  ];
  const { w, d, errors } = await boot(state);

  const menuItem = await openInboxPanel(w, d);
  check(!!menuItem, "the card menu offers Inbox");
  check(!$(d, "#inbox-modal").hidden, "the modal opens");
  check(state.inboxCalls.length === 1 && state.inboxCalls[0] === "cb-a",
        "it asks for THIS chatbot's inbox", state.inboxCalls.join(","));

  const items = $$(d, ".inbox-item");
  check(items.length === 2, "both conversations are listed", `got ${items.length}`);
  check(items[0].textContent.includes("do you ship to canada"), "the preview is shown");
  check(items[0].textContent.includes("4 messages"), "the message count is shown");

  // The row for a visitor who opened the widget and never typed. The server
  // returns it deliberately; a blank line would look like a rendering bug.
  check(items[1].textContent.includes("Opened, but never typed"),
        "a session with no messages says so rather than rendering blank");
  check(items[1].querySelector(".inbox-item__preview--empty") !== null,
        "and is marked as an empty preview");
  check(items[0].textContent.includes("1 message") === false && items[0].textContent.includes("4 messages"),
        "plural agrees with the count");
  check(errors.length === 0, "no page errors", errors.join(" | "));
}

console.log("\n=== reading one conversation ===");
{
  const state = baseState();
  state.inboxRows = [{ id: "v1", preview: "hello", message_count: 2, last_message_at: new Date().toISOString() }];
  state.transcripts.v1 = [
    { role: "user", text: "what is in <b>stock</b>, is it **urgent**?", citations: [] },
    { role: "assistant", text: "We have **plenty**.", citations: [{ key: 1, source_name: "Q3.pdf", text_excerpt: "in stock" }], answered_by: { id: "ag1", name: "Sales" } },
  ];
  const { w, d, errors } = await boot(state);
  await openInboxPanel(w, d);

  check($(d, "#inbox-reader").textContent.includes("Pick a conversation"),
        "the reader prompts before anything is selected");

  click(w, $(d, ".inbox-item"));
  await flush(10);

  check(state.messageCalls.length === 1 && state.messageCalls[0] === "v1",
        "it loads that session's transcript");
  const reader = $(d, "#inbox-reader");
  check($$(reader, ".row").length === 2, "both turns render");
  check($(d, ".inbox-item").classList.contains("inbox-item--active"),
        "the selected conversation is marked active");

  // A visitor typed this on somebody else's website; it is now being shown
  // inside the owner's authenticated app.
  // Neither renderer can turn a visitor's HTML into markup — renderMarkdown
  // builds text nodes against a fixed tag whitelist and never touches
  // innerHTML — so asserting "no <b> element" would pass either way and prove
  // nothing. What separates the two is markdown: a visitor's message is shown
  // exactly as typed, asterisks and all.
  const userRow = reader.querySelector(".row--user");
  check(userRow.querySelector("strong") === null,
        "a visitor's markdown is NOT interpreted");
  check(userRow.textContent.includes("**urgent**"), "the asterisks stay literal");
  check(userRow.querySelector("b") === null && userRow.textContent.includes("<b>stock</b>"),
        "and their HTML is shown as text (true on every path here, by construction)");

  const aiRow = reader.querySelector(".row--assistant");
  check(aiRow.querySelector("strong") !== null, "the assistant's markdown IS rendered");
  check(aiRow.textContent.includes("Q3.pdf"),
        "the owner sees the real filename the visitor saw redacted");
  check(aiRow.querySelector(".agent-badge") !== null, "the answering agent is named");
  check(errors.length === 0, "no page errors", errors.join(" | "));
}

console.log("\n=== empty and failure states ===");
{
  const state = baseState();
  const { w, d } = await boot(state);
  await openInboxPanel(w, d);
  check($(d, "#inbox-list").textContent.includes("No one has used your share link yet"),
        "a chatbot nobody has messaged says so");
  check($$(d, ".inbox-item").length === 0, "and lists nothing");
}
{
  const state = baseState();
  state.inboxStatus = 502;
  const { w, d, errors } = await boot(state);
  await openInboxPanel(w, d);
  check($(d, "#inbox-list").textContent.includes("Could not load"),
        "a failing request is reported, not left on Loading…");
  check(errors.length === 0, "a failing request does not throw", errors.join(" | "));
}

console.log("\n=== it does not leak between chatbots ===");
{
  const state = baseState();
  state.chatbots.push({ id: "cb-b", name: "Beta", description: "", share_token: null });
  state.inboxRows = [{ id: "v1", preview: "alpha question", message_count: 1, last_message_at: new Date().toISOString() }];
  const { w, d } = await boot(state);
  await openInboxPanel(w, d);
  check($(d, "#inbox-list").textContent.includes("alpha question"), "Alpha's inbox loads");

  // Same window it opened in: the panel must blank before the next request
  // resolves, or it shows the previous chatbot's conversations.
  state.inboxRows = [];
  // A real network round trip. Without this the second response lands before
  // anything could observe the stale pane, and the check cannot fail.
  state.slowMs = 60;
  const beta = $$(d, ".bot-card:not(.bot-card--new)").find((c) => c.textContent.includes("Beta"));
  click(w, beta.querySelector(".bot-card__menu"));
  await flush(3);
  click(w, $$(beta, ".bot-card__actions button").find((b) => b.textContent === "Inbox"));
  await flush(3);   // modal open, request still in flight

  check(!$(d, "#inbox-list").textContent.includes("alpha question"),
        "Beta's inbox does not show Alpha's conversation while loading");
  await flush(30);  // let it land
  check(!$(d, "#inbox-list").textContent.includes("alpha question"),
        "nor after it resolves");
  check(state.inboxCalls.join(",") === "cb-a,cb-b", "each open asks for its own chatbot", state.inboxCalls.join(","));
}

console.log("\n=== closing ===");
{
  const state = baseState();
  const { w, d } = await boot(state);
  await openInboxPanel(w, d);
  click(w, $(d, "#inbox-close"));
  await flush(3);
  check($(d, "#inbox-modal").hidden, "Close hides the modal");
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (failed.length) { console.log("FAILED:"); failed.forEach((f) => console.log("  - " + f.label)); process.exit(1); }
