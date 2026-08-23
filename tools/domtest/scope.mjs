// Does per-chat agent exclusion still work after the dashboard replaced the
// sidebar picker? Drives the real UI end to end in a DOM.

import { JSDOM, VirtualConsole } from "jsdom";
import { readFileSync } from "fs";

const FE = "/Users/oscar/Downloads/rag-chatbot/frontend";
const results = [];
const check = (ok, label, detail = "") => {
  results.push({ ok, label, detail });
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
};
const flush = async (n = 25) => { for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0)); };

const state = {
  patches: [],
  chatbots: [
    { id: "cb-1", name: "My chatbot", description: "",
      agents: ["Chem Tutor", "Vision", "RAG"],
      sessions: [{ id: "s1", name: "eyewash", excluded_agent_ids: [] }] },
    { id: "cb-2", name: "Work", description: "",
      agents: ["Contracts"],
      sessions: [{ id: "s9", name: "NDA", excluded_agent_ids: [] }] },
  ],
};

const server = async (url, options = {}) => {
  const method = (options.method || "GET").toUpperCase();
  const [path, qs] = String(url).split("?");
  const cb = new URLSearchParams(qs || "").get("chatbot_id");
  const json = (status, body) => ({ ok: status >= 200 && status < 300, status, statusText: "", json: async () => body });
  const bot = state.chatbots.find((c) => c.id === cb);

  if (path === "/auth/signup-policy") return json(200, { invite_required: false });
  if (path === "/models") return json(200, [{ id: "m" }]);
  if (path === "/chatbots") return json(200, state.chatbots.map(({ id, name, description }) => ({ id, name, description })));
  if (path === "/agents") {
    if (!bot) return json(404, { detail: "Chatbot not found" });
    return json(200, bot.agents.map((n, i) => ({ id: `${cb}-a${i}`, name: n, model: "m", description: "", trained: true })));
  }
  if (path === "/sessions" && method === "GET") {
    if (!bot) return json(404, { detail: "Chatbot not found" });
    return json(200, bot.sessions);
  }
  if (path === "/knowledge/documents") return json(200, []);
  if (/^\/sessions\/[^/]+\/messages$/.test(path)) return json(200, { messages: [] });
  if (/^\/sessions\/[^/]+$/.test(path) && method === "PATCH") {
    const body = JSON.parse(options.body);
    state.patches.push({ id: path.split("/")[2], body });
    return json(200, { excluded_agent_ids: body.excluded_agent_ids || [] });
  }
  return json(404, { detail: "not found" });
};

const vc = new VirtualConsole();
const errors = [];
vc.on("jsdomError", (e) => errors.push(String(e.message || e)));

let html = readFileSync(`${FE}/index.html`, "utf8").replace(/<script src="[^"]*"><\/script>/g, "");
html = html.replace("</head>", `<style>${readFileSync(`${FE}/styles.css`, "utf8")}</style></head>`);
const dom = new JSDOM(html, { runScripts: "dangerously", url: "https://x.test/", virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window, d = w.document;
w.matchMedia = (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false });
w.fetch = server;
w.localStorage.setItem("rag-chat-token", "tok");
w.localStorage.setItem("rag-chat-username", "oscar");
w.confirm = () => true;
w.prompt = () => null;
for (const f of ["theme.js", "markdown.js", "agents.js", "knowledge.js", "scope.js", "chatbots.js", "onboarding.js", "tour-spotlight.js", "tour-steps.js", "tour.js", "dashboard.js", "app.js"]) {
  const el = d.createElement("script");
  el.textContent = readFileSync(`${FE}/${f}`, "utf8");
  d.body.appendChild(el);
}
await flush(40);

const click = async (el, n = 25) => { el.dispatchEvent(new w.MouseEvent("click", { bubbles: true })); await flush(n); };
const $ = (s) => d.querySelector(s);
const $$ = (s) => Array.from(d.querySelectorAll(s));

console.log("\n=== agent exclusion, after the dashboard change ===");

await click($$(".bot-card:not(.bot-card--new)")[0]);
check(!$("#app-view").hidden, "entered a chatbot from the dashboard");
check($("#scope-button").hidden === true, "scope button is hidden with no chat open");

await click($("#session-list li .session-name"));
check(!$("#scope-button").hidden, "scope button appears once a chat is open");
check($("#scope-button").textContent === "All agents", "it starts at 'All agents'", JSON.stringify($("#scope-button").textContent));

await click($("#scope-button"), 10);
check(!$("#scope-modal").hidden, "the scope modal opens");
const boxes = $$("#scope-list input[type=checkbox]");
const labels = $$("#scope-list label").map((l) => l.textContent);
check(boxes.length === 3, "it lists this chatbot's 3 agents", `${boxes.length}: ${labels.join(", ")}`);
check(boxes.every((b) => b.checked), "all start checked");

boxes[1].checked = false;                        // exclude "Vision"
await click($("#scope-save"), 25);
check(state.patches.length === 1, "saving PATCHes the session", JSON.stringify(state.patches[0] || null));
check(
  state.patches[0] && state.patches[0].id === "s1" &&
  JSON.stringify(state.patches[0].body.excluded_agent_ids) === JSON.stringify(["cb-1-a1"]),
  "the excluded agent id is sent for the right chat",
  JSON.stringify(state.patches[0] && state.patches[0].body)
);
check($("#scope-modal").hidden, "the modal closes on save");
check($("#scope-button").textContent === "2 of 3 agents", "the button reflects the narrowed roster",
      JSON.stringify($("#scope-button").textContent));

// The roster must follow the chatbot, not leak across.
await click($("#to-dashboard"));
await click($$(".bot-card:not(.bot-card--new)")[1]);
await click($("#session-list li .session-name"));
await click($("#scope-button"), 10);
const other = $$("#scope-list label").map((l) => l.textContent);
check(other.length === 1 && other[0] === "Contracts",
      "in another chatbot the modal lists ONLY that chatbot's agents", other.join(", "));

// Leaving to the dashboard must not strand an open modal over the grid.
await click($("#scope-button"), 10);   // reopen
await click($("#to-dashboard"));
check($("#scope-modal").hidden, "returning to the dashboard closes an open scope modal");

check(errors.length === 0, "no script errors", errors[0] || "");

const bad = results.filter((r) => !r.ok);
console.log("\n" + "=".repeat(64));
console.log(`${results.length} checks, ${results.length - bad.length} passed, ${bad.length} FAILED`);
bad.forEach((r) => console.log(`  FAILED: ${r.label} ${r.detail}`));
process.exit(bad.length ? 1 : 0);
