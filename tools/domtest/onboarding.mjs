// The getting-started checklist panel, and the ? button that now launches
// the guided tour instead of toggling it.
//
// Loads the real index.html, styles.css and all eight scripts into jsdom,
// stubs the backend (including the /onboarding endpoint), and drives the
// UI. Copied shape from run.mjs: same boot() helper, same fake-server, same
// check()/flush() helpers, plus a per-check-controlled /onboarding payload.
//
// Lives outside the repo on purpose: the project has no dependencies and
// keeps none.
//
// IMPORTANT: never assert visibility with getComputedStyle. jsdom
// special-cases the `hidden` attribute and reports display:none whether or
// not an author rule would really win in a browser — such an assertion
// passes even with the guard rule deleted from the stylesheet. Assert on
// `panel.hidden` instead. Section L of run.mjs covers the CSS cascade for
// this panel statically.

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

// ---------- the onboarding payload helper ----------
const STEPS = ["chatbot", "agent", "description", "knowledge", "answer"];

// `doneCount` steps ticked, in order. Mirrors the server's shape and order.
function payload(doneCount) {
  const steps = STEPS.map((id, i) => ({
    id,
    label: `Step ${id}`,
    hint: `Hint for ${id}`,
    done: i < doneCount,
  }));
  return { steps, complete: doneCount === STEPS.length };
}

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

    if (path === "/onboarding") {
      if (state.failOnboarding) return json(500, { detail: "boom" });
      if (state.malformedOnboarding) return json(200, state.malformedOnboarding);
      return json(200, state.onboardPayload);
    }

    if (path === "/chatbots" && method === "GET") {
      if (state.failChatbots) return json(500, { detail: "boom" });
      return json(200, state.chatbots.map(({ id, name, description }) => ({ id, name, description })));
    }
    if (path.startsWith("/chatbots/") && method === "PATCH") {
      const id = path.split("/")[2];
      const bot = state.chatbots.find((c) => c.id === id);
      bot.name = JSON.parse(options.body).name;
      return json(200, bot);
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

function freshState(onboardPayload = payload(0)) {
  return {
    calls: [],
    failChatbots: false,
    failOnboarding: false,
    malformedOnboarding: null,
    onboardPayload,
    chatbots: [
      { id: "cb-1", name: "My chatbot", description: "Everything from before",
        agents: ["Chem Tutor", "Vision", "RAG"],
        chats: ["eyewash", "BLEU"], docs: ["a.pdf"] },
    ],
  };
}

// ---------- boot the app ----------
async function boot({ state, dismissed = false, breakStorage = false } = {}) {
  consoleErrors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => consoleErrors.push(String(e.message || e)));
  vc.on("error", (m) => consoleErrors.push(String(m)));

  let html = readFileSync(`${FE}/index.html`, "utf8");
  html = html.replace(/<script src="[^"]*"><\/script>/g, ""); // load manually, in order
  const css = readFileSync(`${FE}/styles.css`, "utf8");
  html = html.replace("</head>", `<style>${css}</style></head>`);

  const dom = new JSDOM(html, { runScripts: "dangerously", url: "https://x.test/", virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  w.matchMedia = (q) => ({ matches: false, media: q,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false });
  w.fetch = makeServer(state);
  w.localStorage.setItem("rag-chat-token", "tok");
  w.localStorage.setItem("rag-chat-username", "oscar");
  if (dismissed) w.localStorage.setItem("rag-chat-onboarding-dismissed", "1");

  if (breakStorage) {
    const thrower = () => { throw new Error("storage blocked"); };
    Object.defineProperty(w.localStorage, "getItem", { value: thrower, configurable: true });
  }

  for (const f of ["theme.js", "markdown.js", "agents.js", "knowledge.js",
                   "scope.js", "chatbots.js", "onboarding.js", "tour-spotlight.js",
                   "tour-steps.js", "tour.js", "inbox.js", "dashboard.js", "app.js"]) {
    const el = w.document.createElement("script");
    el.textContent = readFileSync(`${FE}/${f}`, "utf8");
    w.document.body.appendChild(el);
  }
  await flush(40);
  return { dom, w, d: w.document };
}

const $ = (d, s) => d.querySelector(s);
const $$ = (d, s) => Array.from(d.querySelectorAll(s));
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

// =====================================================================
console.log("\n=== onboarding: the getting-started panel ===");

// 1. Auto-shows when steps remain
{
  const state = freshState(payload(1));
  const { d } = await boot({ state });
  const panel = d.getElementById("onboarding");
  check(panel.hidden === false, "1. auto-shows when steps remain", `hidden=${panel.hidden}`);
}

// 2. Stays shut when complete
{
  const state = freshState(payload(5));
  const { d } = await boot({ state });
  const panel = d.getElementById("onboarding");
  check(panel.hidden === true, "2. stays shut when complete", `hidden=${panel.hidden}`);
}

// 3. Stays shut when dismissed
{
  const state = freshState(payload(1));
  const { d } = await boot({ state, dismissed: true });
  const panel = d.getElementById("onboarding");
  check(panel.hidden === true, "3. stays shut when dismissed", `hidden=${panel.hidden}`);
}

// 4. Clicking ? opens the tour, not the checklist panel
{
  const state = freshState(payload(5));
  const { w, d } = await boot({ state });
  const panel = d.getElementById("onboarding");
  click(w, d.getElementById("onboarding-help"));
  await flush(10);
  const tourEl = d.getElementById("tour");
  check(tourEl.hidden === false && panel.hidden === true,
        "4. clicking ? opens the tour and does not open the checklist panel",
        `tourHidden=${tourEl.hidden} panelHidden=${panel.hidden}`);
}

// 5. Every step renders
{
  const state = freshState(payload(2));
  const { d } = await boot({ state });
  const steps = $$(d, ".onboard__step");
  const order = steps.map((s) => s.dataset.step).join(",");
  check(steps.length === 5 && order === STEPS.join(","),
        "5. every step renders in order", `count=${steps.length} order=${order}`);
}

// 6. Done steps are marked
{
  const state = freshState(payload(2));
  const { d } = await boot({ state });
  const done = $$(d, ".onboard__step--done");
  check(done.length === 2, "6. done steps are marked", `${done.length}`);
}

// 7. No hint under a completed step (and one shows under an unfinished step) — the only mode now
{
  const state = freshState(payload(4));
  const { d } = await boot({ state });
  const steps = $$(d, ".onboard__step");
  const firstHasHint = !!steps[0] && !!steps[0].querySelector(".onboard__hint");
  const lastHasHint = !!steps[4] && !!steps[4].querySelector(".onboard__hint");
  check(!firstHasHint, "7a. no hint under a completed step");
  check(lastHasHint, "7b. a hint shows under an unfinished step");
}

// 8. Hiding while steps remain writes the flag
{
  const state = freshState(payload(1));
  const { w, d } = await boot({ state });
  click(w, d.getElementById("onboarding-close"));
  await flush(5);
  const flag = w.localStorage.getItem("rag-chat-onboarding-dismissed");
  check(flag === "1", "8. hiding while steps remain writes the flag", `flag=${flag}`);
}

// 9. A failing /onboarding leaves the dashboard usable
{
  const state = freshState(payload(1));
  state.failOnboarding = true;
  const { d } = await boot({ state });
  const cards = $$(d, ".bot-card:not(.bot-card--new)");
  const panel = d.getElementById("onboarding");
  check(cards.length === 1 && panel.hidden === true && consoleErrors.length === 0,
        "9. a failing /onboarding leaves the dashboard usable",
        `cards=${cards.length} hidden=${panel.hidden} errors=${consoleErrors.length}`);
}

// 10. Storage that throws does not break the dashboard
{
  const state = freshState(payload(1));
  const { d } = await boot({ state, breakStorage: true });
  const cards = $$(d, ".bot-card:not(.bot-card--new)");
  const panel = d.getElementById("onboarding");
  check(cards.length === 1 && panel.hidden === false && consoleErrors.length === 0,
        "10. storage that throws does not break the dashboard",
        `cards=${cards.length} hidden=${panel.hidden} errors=${consoleErrors.length}`);
}

// 11. A 200 with no steps key leaves the dashboard usable and the panel shut
{
  const state = freshState(payload(1));
  state.malformedOnboarding = {};
  const { d } = await boot({ state });
  const cards = $$(d, ".bot-card:not(.bot-card--new)");
  const panel = d.getElementById("onboarding");
  check(cards.length === 1 && panel.hidden === true && consoleErrors.length === 0,
        "11. a 200 with no steps key leaves the dashboard usable",
        `cards=${cards.length} hidden=${panel.hidden} errors=${consoleErrors.length}`);
}

// 12. A 200 with a non-array steps value leaves the dashboard usable and the panel shut
{
  const state = freshState(payload(1));
  state.malformedOnboarding = { steps: null, complete: false };
  const { d } = await boot({ state });
  const cards = $$(d, ".bot-card:not(.bot-card--new)");
  const panel = d.getElementById("onboarding");
  check(cards.length === 1 && panel.hidden === true && consoleErrors.length === 0,
        "12. a 200 with a non-array steps value leaves the dashboard usable",
        `cards=${cards.length} hidden=${panel.hidden} errors=${consoleErrors.length}`);
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
