// Execute the real frontend in a real DOM.
//
// Every check on this feature so far has been static — code traces, node --check,
// id cross-checks. This actually loads index.html, styles.css and all eight
// scripts into jsdom, stubs the backend, and drives the UI.
//
// Lives outside the repo on purpose: the project has no dependencies and keeps none.

import { JSDOM, VirtualConsole } from "jsdom";
import { readFileSync } from "fs";

const FE = "/Users/oscar/Downloads/rag-chatbot/frontend";
const results = [];
let consoleErrors = [];

function check(ok, label, detail = "") {
  results.push({ ok, label, detail });
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
}

const flush = async (n = 12) => {
  for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0));
};

// ---------- the fake backend ----------
function makeServer(state) {
  return async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    const [path, qs] = String(url).split("?");
    const params = new URLSearchParams(qs || "");
    const cb = params.get("chatbot_id");
    const json = (status, body) => ({
      ok: status >= 200 && status < 300,
      status,
      statusText: "",
      json: async () => body,
    });
    state.calls.push(`${method} ${path}${cb ? "?chatbot_id=" + cb : ""}`);

    if (path === "/auth/signup-policy") return json(200, { invite_required: false });
    if (path === "/me") return json(200, { id: "u1", username: "oscar" });
    if (path === "/models") return json(200, [{ id: "claude-sonnet-5" }]);

    if (path === "/chatbots" && method === "GET") {
      if (state.failChatbots) return json(500, { detail: "boom" });
      return json(200, state.chatbots.map(({ id, name, description }) => ({ id, name, description })));
    }
    if (path === "/chatbots" && method === "POST") {
      const body = JSON.parse(options.body);
      const created = { id: "cb-new", name: body.name, description: "", agents: [], chats: [] };
      state.chatbots.push(created);
      return json(201, created);
    }
    if (path.startsWith("/chatbots/") && method === "PATCH") {
      const id = path.split("/")[2];
      const bot = state.chatbots.find((c) => c.id === id);
      bot.name = JSON.parse(options.body).name;
      return json(200, bot);
    }
    if (path.startsWith("/chatbots/") && method === "DELETE") {
      if (state.chatbots.length <= 1) {
        return json(400, { detail: "This is your last chatbot. Create another before deleting it." });
      }
      const id = path.split("/")[2];
      state.chatbots = state.chatbots.filter((c) => c.id !== id);
      return json(204, null);
    }
    if (path === "/agents") {
      const bot = state.chatbots.find((c) => c.id === cb);
      if (!bot) return json(404, { detail: "Chatbot not found" });
      return json(200, bot.agents.map((n, i) => ({ id: `${cb}-a${i}`, name: n, model: "m", description: "", trained: true })));
    }
    if (path === "/sessions" && method === "GET") {
      const bot = state.chatbots.find((c) => c.id === cb);
      if (!bot) return json(404, { detail: "Chatbot not found" });
      return json(200, bot.chats.map((n, i) => ({ id: `${cb}-s${i}`, name: n })));
    }
    if (path === "/knowledge/documents") {
      const bot = state.chatbots.find((c) => c.id === cb);
      if (!bot) return json(404, { detail: "Chatbot not found" });
      return json(200, (bot.docs || []).map((n) => ({ source_id: n, filename: n, status: "indexed" })));
    }
    return json(404, { detail: "not found" });
  };
}

function freshState() {
  return {
    calls: [],
    failChatbots: false,
    chatbots: [
      { id: "cb-1", name: "My chatbot", description: "Everything from before",
        agents: ["Chem Tutor", "Vision", "RAG", "BERT", "GPT-3"],
        chats: ["eyewash", "BLEU"], docs: ["a.pdf", "b.pdf", "c.pdf"] },
      { id: "cb-2", name: "Work", description: "", agents: [], chats: ["NDA"], docs: [] },
      { id: "cb-3", name: "<script>alert(1)</script>", description: "", agents: ["X"], chats: [], docs: [] },
    ],
  };
}

// ---------- boot the app ----------
async function boot({ state, session = null, withCss = true } = {}) {
  consoleErrors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => consoleErrors.push(String(e.message || e)));
  vc.on("error", (m) => consoleErrors.push(String(m)));

  let html = readFileSync(`${FE}/index.html`, "utf8");
  html = html.replace(/<script src="[^"]*"><\/script>/g, ""); // load manually, in order
  if (withCss) {
    const css = readFileSync(`${FE}/styles.css`, "utf8");
    html = html.replace("</head>", `<style>${css}</style></head>`);
  }

  const dom = new JSDOM(html, { runScripts: "dangerously", url: "https://x.test/", virtualConsole: vc });
  const w = dom.window;
  w.matchMedia = (q) => ({ matches: false, media: q,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false });
  w.fetch = makeServer(state);
  w.localStorage.setItem("rag-chat-token", "tok");
  w.localStorage.setItem("rag-chat-username", "oscar");
  w.localStorage.setItem("rag-chat-chatbot", "stale-value");
  if (session) w.sessionStorage.setItem("rag-chat-inside", session);
  w.prompt = (msg, def) => w.__promptReply;
  w.confirm = (msg) => { w.__confirmText = msg; return w.__confirmReply !== false; };
  w.alert = (msg) => { w.__alertText = msg; };

  for (const f of ["theme.js", "markdown.js", "agents.js", "knowledge.js",
                   "scope.js", "chatbots.js", "onboarding.js", "tour-spotlight.js",
                   "tour-steps.js", "dashboard.js", "app.js"]) {
    const el = w.document.createElement("script");
    el.textContent = readFileSync(`${FE}/${f}`, "utf8");
    w.document.body.appendChild(el);
  }
  await flush(40);
  return { dom, w, d: w.document };
}

// Menu entries are chosen by LABEL, never by index: this harness silently
// tested the wrong buttons the day a third entry was added to the card menu.
const menuItem = (card, label) =>
  Array.from(card.querySelectorAll(".bot-card__actions button"))
    .find((b) => b.textContent.trim() === label);

const $ = (d, s) => d.querySelector(s);
const $$ = (d, s) => Array.from(d.querySelectorAll(s));
const shown = (w, el) => el && !el.hidden;

// =====================================================================
console.log("\n=== A. boot and the screen switch ===");
{
  const state = freshState();
  const { w, d } = await boot({ state });
  check(shown(w, $(d, "#dashboard")), "dashboard is visible after login");
  check(!shown(w, $(d, "#app-view")), "chat view is hidden");
  check(!shown(w, $(d, "#auth-gate")), "auth gate is hidden");

  // NOTE: getComputedStyle cannot verify the [hidden] cascade here — jsdom
  // special-cases the hidden attribute and reports "none" whether or not the
  // author rule would win in a real browser. Section L does it statically.

  check(w.localStorage.getItem("rag-chat-chatbot") === null,
        "the retired localStorage key is cleaned up on boot");
  check(consoleErrors.length === 0, "no script errors during boot", consoleErrors[0] || "");
}

console.log("\n=== B. the cards ===");
{
  const state = freshState();
  const { w, d } = await boot({ state });
  const cards = $$(d, ".bot-card:not(.bot-card--new)");
  check(cards.length === 3, "one card per chatbot", `got ${cards.length}`);
  check($$(d, ".bot-card--new").length === 1, "exactly one + New chatbot tile");
  check(d.querySelector(".dashboard__grid").lastElementChild.classList.contains("bot-card--new"),
        "the New tile is last in the grid");

  const first = cards[0];
  const names = $$(first, ".bot-card__agents li").map((li) => li.textContent);
  check(names.join(",") === "Chem Tutor,Vision,RAG", "first three agent names listed", names.join(","));
  check(first.querySelector(".bot-card__more").textContent === "+2 more", "overflow shown as +2 more");
  check(first.querySelector(".bot-card__count").textContent === "2 chats", "chat count rendered");
  check(cards[1].querySelector(".bot-card__none").textContent === "No agents yet",
        "a chatbot with no agents says so");
  check(cards[1].querySelector(".bot-card__count").textContent === "1 chat",
        "singular chat count", cards[1].querySelector(".bot-card__count").textContent);

  // XSS: chatbot named <script>alert(1)</script>
  const evil = cards[2];
  check(evil.querySelector(".bot-card__name").textContent === "<script>alert(1)</script>",
        "hostile chatbot name renders as literal text");
  const injected = Array.from(d.querySelectorAll("script"))
    .filter((s2) => /alert\(1\)/.test(s2.textContent || ""));
  check(injected.length === 0,
        "no executable script was injected by the hostile name",
        `${injected.length} matching script(s)`);
  check(evil.querySelector(".bot-card__name").title === "<script>alert(1)</script>",
        "full name is preserved in the title attribute");
}

console.log("\n=== C. entering and leaving a chatbot ===");
{
  const state = freshState();
  const { w, d } = await boot({ state });
  $$(d, ".bot-card:not(.bot-card--new)")[0].dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(30);
  check(!shown(w, $(d, "#dashboard")) && shown(w, $(d, "#app-view")), "clicking a card opens the chat view");
  check($(d, "#active-title").textContent === "My chatbot", "topbar shows the chatbot name");
  check(w.sessionStorage.getItem("rag-chat-inside") === "cb-1", "resume marker written");
  check($$(d, "#session-list li").length === 2, "that chatbot's chats are in the sidebar",
        `${$$(d, "#session-list li").length}`);

  $(d, "#to-dashboard").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(30);
  check(shown(w, $(d, "#dashboard")) && !shown(w, $(d, "#app-view")), "Dashboard button returns to the grid");
  check(w.sessionStorage.getItem("rag-chat-inside") === null, "resume marker cleared on leaving");
}

console.log("\n=== D. resume after refresh ===");
{
  const state = freshState();
  const { w, d } = await boot({ state, session: "cb-2" });
  check(shown(w, $(d, "#app-view")) && !shown(w, $(d, "#dashboard")),
        "a refresh inside a chatbot resumes there");
  check($(d, "#active-title").textContent === "Work", "resumed into the right chatbot");

  const state2 = freshState();
  const b2 = await boot({ state: state2, session: "cb-gone" });
  check(shown(b2.w, $(b2.d, "#dashboard")), "a stale resume id falls back to the dashboard");
  check(b2.w.sessionStorage.getItem("rag-chat-inside") === null, "and clears the stale marker");
}

console.log("\n=== E. keyboard access (the regression the final review caught) ===");
{
  const state = freshState();
  const { w, d } = await boot({ state });
  const card = $$(d, ".bot-card:not(.bot-card--new)")[0];
  const menuBtn = card.querySelector(".bot-card__menu");

  // Enter on the ⋯ button must NOT navigate into the chatbot.
  menuBtn.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await flush(20);
  check(shown(w, $(d, "#dashboard")),
        "Enter on the ⋯ button does not enter the chatbot (the fix)");

  // And the button still works by click.
  menuBtn.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(5);
  check(!card.querySelector(".bot-card__actions").hidden, "clicking ⋯ opens the menu");

  // Enter on the card itself must still enter.
  const card2 = $$(d, ".bot-card:not(.bot-card--new)")[1];
  card2.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await flush(30);
  check(shown(w, $(d, "#app-view")), "Enter on the card itself still enters the chatbot");
}

console.log("\n=== F. the ⋯ menu ===");
{
  const state = freshState();
  const { w, d } = await boot({ state });
  const cards = $$(d, ".bot-card:not(.bot-card--new)");
  cards[0].querySelector(".bot-card__menu").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(3);
  cards[1].querySelector(".bot-card__menu").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(3);
  check(cards[0].querySelector(".bot-card__actions").hidden, "opening one menu closes the other");
  check(!cards[1].querySelector(".bot-card__actions").hidden, "the second menu is open");

  d.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(3);
  check(cards[1].querySelector(".bot-card__actions").hidden, "clicking the background closes menus");
  check(shown(w, $(d, "#dashboard")), "and does not enter a chatbot");
}

console.log("\n=== G. rename, create, delete ===");
{
  const state = freshState();
  const { w, d } = await boot({ state });
  let cards = $$(d, ".bot-card:not(.bot-card--new)");

  w.__promptReply = "Renamed";
  menuItem(cards[0], "Rename").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(30);
  check($$(d, ".bot-card__name")[0].textContent === "Renamed", "rename updates the card",
        $$(d, ".bot-card__name")[0].textContent);

  w.__promptReply = "Third";
  $(d, ".bot-card--new").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(30);
  check($$(d, ".bot-card:not(.bot-card--new)").length === 4, "new chatbot appears",
        `${$$(d, ".bot-card:not(.bot-card--new)").length}`);

  cards = $$(d, ".bot-card:not(.bot-card--new)");
  w.__confirmReply = true;
  menuItem(cards[0], "Delete").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(40);
  const msg = w.__confirmText || "";
  check(/5 agents/.test(msg) && /2 chats/.test(msg) && /3 documents/.test(msg),
        "delete confirmation names agents, chats AND documents", JSON.stringify(msg.slice(0, 90)));
  check($$(d, ".bot-card:not(.bot-card--new)").length === 3, "the chatbot is gone after deleting");
}

console.log("\n=== H. the last chatbot cannot be deleted ===");
{
  const state = freshState();
  state.chatbots = [state.chatbots[0]];
  const { w, d } = await boot({ state });
  w.__confirmReply = true;
  menuItem($(d, ".bot-card:not(.bot-card--new)"), "Delete")
    .dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(40);
  const status = $(d, "#dashboard-status").textContent;
  check(/last chatbot/i.test(status), "the server's own message is shown", JSON.stringify(status));
  check($$(d, ".bot-card:not(.bot-card--new)").length === 1, "nothing was deleted");
}

console.log("\n=== I. a failed load offers a way out ===");
{
  const state = freshState();
  state.failChatbots = true;
  const { w, d } = await boot({ state });
  const retry = $(d, ".bot-card--new");
  check(!!retry && /try again/i.test(retry.textContent), "a retry control is rendered",
        retry ? JSON.stringify(retry.textContent) : "none");
  check(/could not load/i.test($(d, "#dashboard-status").textContent), "an error message is shown");
  state.failChatbots = false;
  retry.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(40);
  check($$(d, ".bot-card:not(.bot-card--new)").length === 3, "retry recovers the grid",
        `${$$(d, ".bot-card:not(.bot-card--new)").length}`);
}

console.log("\n=== J. theme toggle on the dashboard ===");
{
  const state = freshState();
  const { w, d } = await boot({ state });
  const toggles = $$(d, ".theme-toggle");
  check(toggles.length >= 2, "a theme toggle exists on the dashboard as well as the chat view",
        `${toggles.length} found`);
  const before = d.documentElement.getAttribute("data-theme");
  const dashToggle = $(d, ".dashboard__head .theme-toggle") || toggles[0];
  dashToggle.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(5);
  const after = d.documentElement.getAttribute("data-theme");
  check(after !== before, "clicking it changes the theme", `${before} -> ${after}`);
}

console.log("\n=== K. request budget ===");
{
  const state = freshState();
  await boot({ state });
  const n = state.calls.filter((c) => c === "GET /chatbots").length;
  check(n === 1, "GET /chatbots is fetched once on a dashboard login", `${n} times`);
}

console.log("\n=== L. hidden elements vs the CSS cascade (static) ===");
{
  // The bug class that shipped twice: an author rule setting `display` outranks
  // the UA's [hidden] { display:none }, so `el.hidden = true` sets the property
  // and the element renders anyway.
  //
  // jsdom CANNOT see this — it special-cases the hidden attribute rather than
  // running the cascade, so getComputedStyle returns "none" either way. So this
  // combines runtime discovery (which elements the app actually hides) with a
  // static read of the real stylesheet (where the bug lives).
  const cssText = readFileSync(`${FE}/styles.css`, "utf8");

  const setsDisplay = (sel) => {
    const re = new RegExp(`(^|[,}])\\s*${sel.replace(/[.#]/g, "\\$&")}\\s*(,[^{]*)?\\{([^}]*)\\}`, "m");
    const m = cssText.match(re);
    if (!m) return null;
    const dm = m[3].match(/(^|;)\s*display\s*:\s*([a-z-]+)/);
    return dm ? dm[2] : null;
  };
  const hasGuard = (sel) =>
    new RegExp(`${sel.replace(/[.#]/g, "\\$&")}\\[hidden\\]`).test(cssText);

  const selectorsOf = (el) => [
    ...(el.id ? ["#" + el.id] : []),
    ...String(el.className || "").split(/\s+/).filter(Boolean).map((c) => "." + c),
  ];

  const audit = (label) => {
    const offenders = [];
    for (const el of Array.from(d.querySelectorAll("[hidden]"))) {
      for (const sel of selectorsOf(el)) {
        const disp = setsDisplay(sel);
        if (disp && disp !== "none" && !hasGuard(sel)) {
          offenders.push(`${sel} sets display:${disp} with no ${sel}[hidden] guard`);
        }
      }
    }
    check(offenders.length === 0, `no hidden element outranks [hidden] (${label})`,
          [...new Set(offenders)].join("; "));
  };

  const state = freshState();
  const { w, d } = await boot({ state });
  audit("on the dashboard");

  const card = $$(d, ".bot-card:not(.bot-card--new)")[0];
  card.querySelector(".bot-card__menu").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(5);
  audit("with a card menu open");

  $$(d, ".bot-card:not(.bot-card--new)")[1].dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush(30);
  audit("inside a chatbot");
}

// =====================================================================
const bad = results.filter((r) => !r.ok);
console.log("\n" + "=".repeat(72));
console.log(`${results.length} checks, ${results.length - bad.length} passed, ${bad.length} FAILED`);
if (bad.length) {
  console.log("\nFAILURES:");
  bad.forEach((r) => console.log(`  ${r.label}${r.detail ? "  — " + r.detail : ""}`));
}
process.exit(bad.length ? 1 : 0);
