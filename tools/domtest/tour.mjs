// The guided tour engine (tour.js) and its wiring — zero coverage before this
// file. Same shape as onboarding.mjs: boot the real index.html/styles.css and
// all eight scripts into jsdom, stub the backend including /onboarding, drive
// the UI for real.
//
// Lives outside the repo on purpose: the project has no dependencies and
// keeps none.
//
// IMPORTANT: never assert visibility with getComputedStyle. jsdom
// special-cases the `hidden` attribute and reports display:none whether or
// not an author rule would really win in a browser — such an assertion
// passes even with the guard rule deleted. Assert on `el.hidden` instead.
//
// A second jsdom limitation matters more here than anywhere else in this
// harness: jsdom has no layout engine, so getBoundingClientRect() always
// returns a zero-size rect, and tourVisible() — which gates every "doing"
// step's auto-advance and the not-on-screen recovery branches in tick() — is
// therefore never true here, regardless of real DOM state. That makes two
// things true of every check below: (1) none of them can exercise a step's
// TOUR_DONE predicate actually firing an auto-advance (that code path is
// simply unreachable in jsdom without faking layout, which would test the
// fake more than the engine); (2) the "not visible" branch's *surface*
// mismatch/rewind logic runs on every tick regardless, so a check that opens
// the tour while sitting on the wrong surface for its target step gets
// silently rewound before the test ever sees the intended step. Check 5
// and check 10 route around this by first navigating into a real chatbot
// (so currentSurface() reads "chat"), matching every step in play.

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
      return json(200, state.onboardPayload);
    }

    if (path === "/chatbots" && method === "GET") {
      return json(200, state.chatbots.map(({ id, name, description }) => ({ id, name, description })));
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
    failOnboarding: false,
    onboardPayload,
    chatbots: [
      { id: "cb-1", name: "My chatbot", description: "Everything from before",
        agents: ["Chem Tutor", "Vision", "RAG"],
        chats: ["eyewash", "BLEU"], docs: ["a.pdf"] },
    ],
  };
}

// ---------- boot the app ----------
async function boot({ state } = {}) {
  consoleErrors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => consoleErrors.push(String(e.message || e)));
  vc.on("error", (m) => consoleErrors.push(String(m)));

  let html = readFileSync(`${FE}/index.html`, "utf8");
  html = html.replace(/<script src="[^"]*"><\/script>/g, ""); // load manually, in order
  const css = readFileSync(`${FE}/styles.css`, "utf8");
  html = html.replace("</head>", `<style>${css}</style></head>`);

  // pretendToBeVisual is load-bearing: without it window.requestAnimationFrame
  // does not exist, and the tour engine throws the instant it opens.
  const dom = new JSDOM(html, { runScripts: "dangerously", url: "https://x.test/", virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  w.matchMedia = (q) => ({ matches: false, media: q,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false });
  w.fetch = makeServer(state);
  w.localStorage.setItem("rag-chat-token", "tok");
  w.localStorage.setItem("rag-chat-username", "oscar");

  // jsdom has no layout engine: every getBoundingClientRect is 0x0, which
  // pins the tour's tourVisible() gate permanently closed and means tick()
  // never runs its main branch (paintPanes, the "doing" auto-advance). Give
  // non-hidden elements a plausible rect so the engine actually executes;
  // keep 0x0 for hidden ones so the visibility logic still discriminates.
  // Must be installed before the scripts run, since tour.js reads elements
  // that exist from first load.
  w.Element.prototype.getBoundingClientRect = function () {
    const zero = { x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 };
    if (this.hidden || (this.closest && this.closest("[hidden]"))) return zero;
    return { x: 100, y: 100, width: 200, height: 40,
             top: 100, left: 100, right: 300, bottom: 140 };
  };
  if (!w.HTMLElement.prototype.scrollIntoView) {
    w.HTMLElement.prototype.scrollIntoView = function () {};
  }

  for (const f of ["theme.js", "markdown.js", "agents.js", "knowledge.js",
                   "scope.js", "chatbots.js", "onboarding.js", "tour-spotlight.js",
                   "tour-steps.js", "tour.js", "dashboard.js", "app.js"]) {
    const el = w.document.createElement("script");
    el.textContent = readFileSync(`${FE}/${f}`, "utf8");
    w.document.body.appendChild(el);
  }
  await flush(40);
  return { dom, w, d: w.document };
}

const $ = (d, s) => d.querySelector(s);
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

// Enters the first real chatbot from the dashboard grid — puts the app on
// the "chat" surface, the way a real visit would before description/
// knowledge/ask steps ever come up.
async function enterChatbot(w, d) {
  click(w, $(d, ".bot-card:not(.bot-card--new)"));
  await flush(20);
}

// =====================================================================
console.log("\n=== tour: the guided tour engine ===");

// 1. Clicking ? opens the tour
{
  const state = freshState(payload(0));
  const { w, d } = await boot({ state });
  click(w, d.getElementById("onboarding-help"));
  await flush(10);
  const tourEl = d.getElementById("tour");
  check(tourEl.hidden === false, "1. clicking ? opens the tour", `hidden=${tourEl.hidden}`);
}

// 2. All five onboarding steps done: tour still opens, at step 1
{
  const state = freshState(payload(5));
  const { w, d } = await boot({ state });
  click(w, d.getElementById("onboarding-help"));
  await flush(10);
  const progress = d.getElementById("tour-progress").textContent;
  check(progress === "1 of 8", "2. with everything done the tour still opens, at step 1", progress);
}

// 3. A fully set-up account is never told to create anything
{
  const state = freshState(payload(5));
  const { w, d } = await boot({ state });
  click(w, d.getElementById("onboarding-help"));
  await flush(10);
  const body = d.getElementById("tour-body").textContent;
  const badPrefixes = ["Click ", "Create ", "Upload ", "Open "];
  const bad = badPrefixes.find((p) => body.startsWith(p));
  check(!bad, "3. a fully set-up account is never told to create anything", `body="${body}"`);
}

// 4. #tour-next advances
{
  const state = freshState(payload(5));
  const { w, d } = await boot({ state });
  click(w, d.getElementById("onboarding-help"));
  await flush(10);
  click(w, d.getElementById("tour-next"));
  await flush(10);
  const progress = d.getElementById("tour-progress").textContent;
  check(progress === "2 of 8", "4. tour-next advances", progress);
}

// 5. chatbot + agent done, description not: opens on the description step,
// not step 1. Requires being on the chat surface first (see file header) —
// #onboarding-help itself lives inside the dashboard and is unreachable from
// chat, so this one calls startTour() directly, exactly the way an autoplay
// handoff would while already inside a chatbot.
{
  const state = freshState(payload(2)); // chatbot, agent done; description not
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);
  await w.startTour();
  await flush(10);
  const progress = d.getElementById("tour-progress").textContent;
  const title = d.getElementById("tour-title").textContent;
  check(progress === "5 of 8" && title === "Describe it",
        "5. opens on the description step rather than step 1",
        `progress=${progress} title="${title}"`);
}

// 6. #tour-skip hides the tour and writes the skip flag
{
  const state = freshState(payload(0));
  const { w, d } = await boot({ state });
  click(w, d.getElementById("onboarding-help"));
  await flush(10);
  click(w, d.getElementById("tour-skip"));
  await flush(10);
  const tourEl = d.getElementById("tour");
  const flag = w.localStorage.getItem("rag-chat-tour-skipped");
  check(tourEl.hidden === true && flag === "1",
        "6. tour-skip hides the tour and writes the skip flag",
        `hidden=${tourEl.hidden} flag=${flag}`);
}

// 7. Escape ends the tour
{
  const state = freshState(payload(0));
  const { w, d } = await boot({ state });
  click(w, d.getElementById("onboarding-help"));
  await flush(10);
  d.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await flush(10);
  const tourEl = d.getElementById("tour");
  check(tourEl.hidden === true, "7. Escape ends the tour", `hidden=${tourEl.hidden}`);
}

// 8. Clicking ? opens the tour even when already marked skipped
{
  const state = freshState(payload(0));
  const { w, d } = await boot({ state });
  w.localStorage.setItem("rag-chat-tour-skipped", "1");
  click(w, d.getElementById("onboarding-help"));
  await flush(10);
  const tourEl = d.getElementById("tour");
  check(tourEl.hidden === false,
        "8. clicking ? opens the tour even when already skipped",
        `hidden=${tourEl.hidden}`);
}

// 9. A 500 from /onboarding still opens the tour, with no console error
{
  const state = freshState(payload(0));
  state.failOnboarding = true;
  const { w, d } = await boot({ state });
  click(w, d.getElementById("onboarding-help"));
  await flush(10);
  const tourEl = d.getElementById("tour");
  check(tourEl.hidden === false && consoleErrors.length === 0,
        "9. a 500 from /onboarding still opens the tour",
        `hidden=${tourEl.hidden} errors=${consoleErrors.length}`);
}

// 10. Five Next clicks leave exactly one live rAF loop.
// showStep() cancels the pending frame before re-entering tick() — without
// that cancel, every Next click leaked a loop, and two loops racing in one
// frame could both see a "doing" predicate flip and both advance, skipping
// a step the user never saw. Wrap requestAnimationFrame/cancelAnimationFrame
// with counters (a full fake, not a passthrough, so nothing auto-fires and
// only explicit clicks drive the engine) and assert cancelled === 5.
//
// Needs the chat surface for the same reason check 5 does: steps 2 through 7
// all live on "chat", so five clicks from step 1 walk 0→1→2→3→4→5 with no
// surface-mismatch rewind in the way, giving an exact, predictable count.
{
  const state = freshState(payload(5));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);

  let requested = 0;
  let cancelled = 0;
  let nextId = 1;
  w.requestAnimationFrame = () => { requested += 1; return nextId++; };
  w.cancelAnimationFrame = () => { cancelled += 1; };

  await w.startTour();
  await flush(10);
  for (let i = 0; i < 5; i += 1) {
    click(w, d.getElementById("tour-next"));
    await flush(5);
  }
  check(cancelled === 5,
        "10. five Next clicks leave exactly one live rAF loop",
        `cancelled=${cancelled} requested=${requested}`);
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
