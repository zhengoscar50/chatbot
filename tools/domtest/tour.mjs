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
// harness: jsdom has no layout engine, so getBoundingClientRect() would
// otherwise always return a zero-size rect, which pins tourVisible() —
// gating every "doing" step's auto-advance and the not-on-screen recovery
// branches in tick() — permanently false regardless of real DOM state, and
// the engine's main loop would never run under test. boot() below stubs
// Element.prototype.getBoundingClientRect (and scrollIntoView) to model
// visibility instead: a plausible rect for anything not hidden, a zero rect
// for anything hidden or under a [hidden] ancestor. That is what lets
// tourVisible() actually discriminate and the engine's main loop — auto-
// advance, paintPanes, the surface rewind, the step-8 fallback — run for
// real here rather than being permanently unreachable.

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
// Needs the chat surface, and needs to START there: indices 2..7 (agents,
// new-agent, description, knowledge, ask, who-answered) all live on "chat",
// so five clicks from index 2 walk 2→3→4→5→6→7 entirely within one surface.
// Index 1 ("enter") is a DASHBOARD step, and Next deliberately refuses to
// cross a surface the user has not reached, so starting earlier would block
// on the boundary rather than counting frames.
{
  const state = freshState(payload(5));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);

  await w.startTour();
  w.showStep(2); // first step on the chat surface
  await flush(10);

  // Counters go in AFTER the setup: startTour and showStep each legitimately
  // cancel a frame of their own, and this check is about what the five clicks
  // do, not about the walk to the starting line.
  let requested = 0;
  let cancelled = 0;
  let nextId = 1;
  w.requestAnimationFrame = () => { requested += 1; return nextId++; };
  w.cancelAnimationFrame = () => { cancelled += 1; };

  for (let i = 0; i < 5; i += 1) {
    click(w, d.getElementById("tour-next"));
    await flush(5);
  }
  check(cancelled === 5,
        "10. five Next clicks leave exactly one live rAF loop",
        `cancelled=${cancelled} requested=${requested}`);
}

// 15. Doing what a step asks carries you forward, even when the step was
// phrased as a caption rather than an instruction. Reported from a real
// session: on step 2 the tour sat there after the user clicked into the
// chatbot, still highlighting the dashboard. Two causes, both fixed —
// advancing was gated on the copy mode ("showing" steps never advanced), and
// the completion test ran only while the step's own target was still visible,
// which entering a chatbot destroys. Whoever owns the chatbot already, so
// step 2 renders in "showing" mode: that is the case that was broken.
{
  const state = freshState(payload(1)); // has a chatbot, nothing else
  const { w, d } = await boot({ state });
  await w.startTour();
  w.showStep(1); // "enter" — Go inside
  await flush(10);
  const before = d.getElementById("tour-progress").textContent;

  await enterChatbot(w, d);
  await flush(20);
  const after = d.getElementById("tour-progress").textContent;

  check(before === "2 of 8" && after === "3 of 8",
        "15. entering the chatbot advances the tour on its own",
        `before=${before} after=${after}`);
}

// 16. Next refuses to cross onto a surface the user has not reached, and says
// so instead of looking dead. Same session: pressing Next on step 2 appeared
// to do nothing, because it advanced to a chat-surface step whose target does
// not exist on the dashboard and the rewind pulled it straight back. Steps
// 3-8 are genuinely unreachable until you enter a chatbot, so the honest
// behaviour is a disabled button carrying the reason.
{
  const state = freshState(payload(1));
  const { w, d } = await boot({ state });
  await w.startTour();
  w.showStep(1); // "enter" is the last dashboard step; step 3 is on "chat"
  await flush(10);
  const next = d.getElementById("tour-next");
  const disabled = next.disabled;
  const title = next.title;

  click(w, next);
  await flush(10);
  const progress = d.getElementById("tour-progress").textContent;

  check(disabled === true && title.length > 0 && progress === "2 of 8",
        "16. Next is disabled at a surface boundary rather than silently dead",
        `disabled=${disabled} title="${title}" progress=${progress}`);
}

// 17. A step whose target sits behind an open modal waits and says so, rather
// than cutting a hole around a control the click cannot reach. Reported from a
// real session: "step 6 and beyond doesn't work if the user is on the agent
// screen". The tour paints above modals (z-index 60 vs 20), so #my-knowledge —
// a sidebar button, not in any modal — still got highlighted while the agent
// form covered it, and clicking the hole hit the modal.
{
  const state = freshState(payload(1));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);
  d.getElementById("agent-list-modal").hidden = false;
  d.getElementById("agent-modal").hidden = false;
  await w.startTour();
  w.showStep(5); // "knowledge" -> #my-knowledge, outside every modal
  await flush(15);
  const occludedBody = d.getElementById("tour-body").textContent;

  d.getElementById("agent-modal").hidden = true;
  d.getElementById("agent-list-modal").hidden = true;
  await flush(15);
  const freeBody = d.getElementById("tour-body").textContent;

  check(/close this window/i.test(occludedBody) && /upload/i.test(freeBody),
        "17. a target behind a modal waits, then recovers its own copy",
        `occluded="${occludedBody.slice(0, 34)}" free="${freeBody.slice(0, 34)}"`);
}

// 18. The description step completes when the FORM is done, not on the first
// keystroke. Same session: typing one character marched the user out of a
// half-filled agent form into a step pointing at the sidebar behind it.
{
  const state = freshState(payload(1));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);
  d.getElementById("agent-list-modal").hidden = false;
  d.getElementById("agent-modal").hidden = false;
  await w.startTour();
  w.showStep(4); // "description"
  await flush(15);

  d.getElementById("agent-description").value = "Chemistry questions";
  await flush(20);
  const whileTyping = d.getElementById("tour-progress").textContent;

  d.getElementById("agent-modal").hidden = true; // saved
  await flush(20);
  const afterSaving = d.getElementById("tour-progress").textContent;

  check(whileTyping === "5 of 8" && afterSaving === "6 of 8",
        "18. the description step holds until the agent form closes",
        `typing=${whileTyping} saved=${afterSaving}`);
}

// 19. The description latch is per-step, not per-tour. Once step 5 has been
// completed the latch is set; if it survived into a later step, coming BACK to
// step 5 — which the step-8 fallback button does — would satisfy it instantly
// against a closed form and skip the very step the user was sent to fix.
{
  const state = freshState(payload(1));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);
  const modal = d.getElementById("agent-modal");

  await w.startTour();
  w.showStep(4);                       // description
  modal.hidden = false; await flush(25);   // form opens: latch arms
  modal.hidden = true;  await flush(35);   // form closes: step completes
  const advanced = d.getElementById("tour-progress").textContent;

  w.showStep(4);                       // sent back, form still closed
  await flush(35);
  const returned = d.getElementById("tour-progress").textContent;

  check(advanced === "6 of 8" && returned === "5 of 8",
        "19. returning to the description step does not skip on a stale latch",
        `advanced=${advanced} returned=${returned}`);
}

// 20. The tour is a walkthrough, not a setup wizard. Someone should be able to
// read all eight steps without committing to anything — no agent created, no
// document uploaded, no message sent. Reported from a real session: "the user
// doesn't need to create a specialist agent on the spot". Entering a chatbot
// is the one unavoidable action, and it creates nothing.
{
  const state = freshState(payload(1));
  const { w, d } = await boot({ state });
  await w.startTour();
  await flush(15);

  const reached = [];
  for (let i = 0; i < 12; i += 1) {
    const btn = d.getElementById("tour-next");
    reached.push(d.getElementById("tour-progress").textContent);
    if (btn.disabled) {
      await enterChatbot(w, d);       // navigation only; nothing is created
      await flush(20);
      continue;
    }
    click(w, btn);
    await flush(20);
    if (d.getElementById("tour-progress").textContent === "8 of 8") break;
  }
  const end = d.getElementById("tour-progress").textContent;

  check(end === "8 of 8",
        "20. the whole tour can be walked without creating anything",
        `ended at ${end} via ${reached.join(" ")}`);
}

// 21. A step whose target is merely NOT OPEN YET keeps its own explanation up
// rather than telling the user to close a window. Same session: pressing Next
// past the agent form landed on the description step, which said "Close this
// window to carry on" — advice for the opposite problem. The description
// lesson is the most valuable copy in the tour; it should still be readable
// by someone who declined to create an agent.
{
  const state = freshState(payload(1));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);
  d.getElementById("agent-list-modal").hidden = false;  // open; agent form is NOT
  await w.startTour();
  w.showStep(4);                                        // description
  await flush(25);
  const body = d.getElementById("tour-body").textContent;

  // Same freeze hazard as check 24, by a different route: a step that cannot
  // paint must not leave the previous step's panes covering the page.
  const blocking = ["top", "right", "bottom", "left"]
    .map((k) => parseInt(d.getElementById(`tour-pane-${k}`).style.width, 10) || 0)
    .reduce((a, b) => a + b, 0);

  check(/rout/i.test(body) && !/close this window/i.test(body) && blocking === 0,
        "21. a not-yet-open target keeps its explanation and blocks nothing",
        `body="${body.slice(0, 40)}" panes=${blocking}`);
}

// 22. The user can run AHEAD of the tour, and the tour has to catch up rather
// than lock. Reported from a real session: clicking a chatbot while step 1 was
// still describing the grid stranded the tour on a dashboard step forever —
// Next could not cross onto the surface the user had already reached, and the
// box kept explaining the dashboard to someone looking at a chatbot. The
// surface search used to scan backward only, which finds nothing when every
// earlier step is on the surface you just left.
{
  const state = freshState(payload(1));
  const { w, d } = await boot({ state });
  await w.startTour();
  w.showStep(0);                 // step 1, "grid" — a dashboard step
  await flush(15);
  const before = d.getElementById("tour-progress").textContent;

  await enterChatbot(w, d);      // jump the queue
  await flush(30);
  const after = d.getElementById("tour-progress").textContent;
  const stuck = d.getElementById("tour-next").disabled;

  // Lands on the first chat step (3), skipping "Go inside" — already done.
  check(before === "1 of 8" && after === "3 of 8" && stuck === false,
        "22. running ahead of the tour makes it catch up, not lock",
        `before=${before} after=${after} nextDisabled=${stuck}`);
}

// 23. The knowledge step is finished by CLOSING the panel, not by opening it,
// and while it is open the step keeps its own instructions. Reported from a
// real session: "there is no change from the step 6 add text to the chatbot
// part" — the step completed the instant the panel appeared, so "Upload a PDF"
// was satisfied by merely looking at it. And because the panel then covers
// #my-knowledge, the generic occlusion notice would have replaced the upload
// instructions with "close this window" at the exact moment the user was meant
// to be reading them.
{
  const state = freshState(payload(1));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);
  const panel = d.getElementById("knowledge-modal");

  await w.startTour();
  w.showStep(5);                       // knowledge
  await flush(20);
  const shut = d.getElementById("tour-progress").textContent;

  panel.hidden = false;                // opened: must NOT complete
  await flush(25);
  const openProgress = d.getElementById("tour-progress").textContent;
  const openBody = d.getElementById("tour-body").textContent;

  panel.hidden = true;                 // finished with it
  await flush(30);
  const afterProgress = d.getElementById("tour-progress").textContent;

  check(shut === "6 of 8" && openProgress === "6 of 8"
        && /upload/i.test(openBody) && !/close this window/i.test(openBody)
        && afterProgress === "7 of 8",
        "23. the knowledge step holds while its panel is open, then advances",
        `shut=${shut} open=${openProgress} after=${afterProgress} body="${openBody.slice(0, 30)}"`);
}

// 24. When the tour is not highlighting anything it must not be BLOCKING
// anything either. Reported from a real session: "for step 6 its locked so i
// cant actually close out of the agent screen to continue". The four panes are
// what make everything except the hole unclickable, and every non-painting
// path returned without touching them — so they kept the previous step's
// geometry and covered the very Close button the box was telling the user to
// press. Waiting silently is bad; freezing the page is worse.
{
  const state = freshState(payload(1));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);
  d.getElementById("agent-list-modal").hidden = false;

  await w.startTour();
  w.showStep(4);                                  // description
  d.getElementById("agent-modal").hidden = false; // its own panel: painted
  await flush(25);
  const painting = ["top", "right", "bottom", "left"]
    .map((k) => parseInt(d.getElementById(`tour-pane-${k}`).style.width, 10) || 0)
    .reduce((a, b) => a + b, 0);

  d.getElementById("agent-modal").hidden = true;  // -> step 6, target occluded
  await flush(30);
  const blocking = ["top", "right", "bottom", "left"]
    .map((k) => parseInt(d.getElementById(`tour-pane-${k}`).style.width, 10) || 0)
    .reduce((a, b) => a + b, 0);

  check(painting > 0 && blocking === 0,
        "24. panes collapse when nothing is highlighted, so the page stays usable",
        `whilePainting=${painting} whileWaiting=${blocking}`);
}

// 25. The two entry points are not the same. Autoplay after signup shows a
// person who has seen nothing the tour from step 1; pressing ? later is a
// replay and resumes at the first step with work left. Checked against a real
// fresh account: signup creates one starter chatbot, so "create a chatbot" is
// already done and the resume logic would otherwise open at "Go inside" —
// skipping the dashboard orientation for the newest possible user.
{
  const state = freshState(payload(1)); // exactly what a fresh signup derives
  const { w, d } = await boot({ state });

  w.sessionStorage.setItem("rag-chat-tour-autoplay", "1");
  w.tourAutoplayIfFlagged();
  await flush(30);
  const autoplay = d.getElementById("tour-progress").textContent;

  w.skipTour();
  await flush(10);
  click(w, d.getElementById("onboarding-help"));
  await flush(30);
  const replay = d.getElementById("tour-progress").textContent;

  check(autoplay === "1 of 8" && replay === "2 of 8",
        "25. signup starts at step 1; ? resumes where there is work left",
        `autoplay=${autoplay} replay=${replay}`);
}

// 11. Step 8's fallback -- the engine's own comments call this "the tour's
// most valuable moment". The server ALWAYS sends answered_by; what varies is
// its id, which is null for the general assistant and set for a specialist.
// So the badge always renders, and the discriminator is the
// agent-badge--general class, not the badge's absence. An earlier version of
// this check rendered a row with no badge at all -- a state the app cannot
// produce -- which is why it passed against an implementation whose fallback
// could never fire. Feed appendMessage exactly what the server sends.
{
  const state = freshState(payload(5));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);
  w.appendMessage("assistant", "AI", "General answer, no specialist matched.",
                  [], { id: null, name: "General assistant" });
  await w.startTour();
  w.showStep(7); // last step, "who-answered" (TOUR_STEPS has 8 entries)
  await flush(15);
  const title = d.getElementById("tour-title").textContent;
  const actionHidden = d.getElementById("tour-action").hidden;
  check(title === "The general assistant answered" && actionHidden === false,
        "11a. step 8 falls back when no specialist answered",
        `title="${title}" actionHidden=${actionHidden}`);
  click(w, d.getElementById("tour-action"));
  await flush(10);
  const progress = d.getElementById("tour-progress").textContent;
  check(progress === "5 of 8",
        "11b. tour-action from the fallback lands on the description step",
        progress);
}

// 12. The same step shows its normal copy when a specialist DID answer --
// without this, check 11 proves nothing (it could just be that #tour-action
// is always shown on step 8).
{
  const state = freshState(payload(5));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);
  w.appendMessage("assistant", "AI", "Specialist answer.", [],
                  { id: "a1", name: "Chem Tutor" });
  await w.startTour();
  w.showStep(7); // last step, "who-answered" (TOUR_STEPS has 8 entries)
  await flush(15);
  const title = d.getElementById("tour-title").textContent;
  const actionHidden = d.getElementById("tour-action").hidden;
  check(title === "A specialist answered" && actionHidden === true,
        "12. the same step shows normal copy when a specialist answered",
        `title="${title}" actionHidden=${actionHidden}`);
}

// 13. Leaving the chat surface mid-tour rewinds to a dashboard-surface step
// rather than sitting on a target that can no longer appear. Force the tour
// onto "agents" (index 2, surface: chat), then use the real "Dashboard"
// button -- not a direct hidden-attribute poke -- to leave chat.
// lastStepOnSurface walks backward from the current index for a step whose
// surface matches; from index 2 that lands on index 1 ("enter", surface:
// dashboard), so tour-progress should read "2 of 8", not stay put or reset
// to "1 of 8".
{
  const state = freshState(payload(5));
  const { w, d } = await boot({ state });
  await enterChatbot(w, d);
  await w.startTour();
  w.showStep(2); // "agents" -- surface: chat
  await flush(10);
  check(d.getElementById("tour-progress").textContent === "3 of 8",
        "13 setup: tour is on the chat-surface step before leaving",
        d.getElementById("tour-progress").textContent);
  click(w, d.getElementById("to-dashboard"));
  await flush(60);
  const progress = d.getElementById("tour-progress").textContent;
  check(progress === "2 of 8",
        "13. leaving the chat surface rewinds to the last dashboard-surface step",
        progress);
}

// 14. Autoplay is a one-shot handoff, consumed by tourAutoplayIfFlagged()'s
// own removeItem -- nothing else clears the flag. Flag it, run
// loadDashboard(), confirm the tour opened; end the tour, run loadDashboard()
// again, and confirm it did NOT reopen (if removeItem were missing, the
// still-set flag would fire startTour() on every single dashboard load).
{
  const state = freshState(payload(5));
  const { w, d } = await boot({ state });
  w.sessionStorage.setItem("rag-chat-tour-autoplay", "1");
  await w.loadDashboard();
  await flush(15);
  const openedFirst = d.getElementById("tour").hidden === false;
  w.endTour();
  await flush(5);
  await w.loadDashboard();
  await flush(15);
  const stayedClosedSecondTime = d.getElementById("tour").hidden === true;
  check(openedFirst && stayedClosedSecondTime,
        "14. autoplay fires once, not on every dashboard load",
        `openedFirst=${openedFirst} reopenedOnSecondLoad=${!stayedClosedSecondTime}`);
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
