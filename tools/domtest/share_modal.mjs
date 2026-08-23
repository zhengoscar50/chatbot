// Reproduce the reported bug: the Share modal hands you a link that is not the
// live one, and tells you the copy succeeded.
//
// Run BEFORE the fix to see it fail, and after to see it pass.

import { JSDOM, VirtualConsole } from "jsdom";
import { readFileSync } from "fs";

const FE = "/Users/oscar/Downloads/rag-chatbot/frontend";
const results = [];
const check = (ok, label, detail = "") => {
  results.push({ ok, label });
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
};
const flush = async (n = 25) => { for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0)); };

// A share endpoint that is deliberately SLOW, so the window between "modal
// visible" and "fields populated" is observable — which is exactly the window
// a real network request opens.
let slowMs = 0;
const clipboard = { written: [] };

function makeServer(state) {
  return async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    const [path, qs] = String(url).split("?");
    const cb = new URLSearchParams(qs || "").get("chatbot_id");
    const json = (status, body) => ({ ok: status >= 200 && status < 300, status, statusText: "", json: async () => body });

    if (path === "/auth/signup-policy") return json(200, { invite_required: false });
    if (path === "/chatbots" && method === "GET") return json(200, state.chatbots);
    if (path === "/agents") return json(200, []);
    if (path === "/sessions") return json(200, []);
    if (path === "/knowledge/documents") return json(200, []);
    if (/^\/chatbots\/[^/]+\/share$/.test(path)) {
      const id = path.split("/")[2];
      if (slowMs) await new Promise((r) => setTimeout(r, slowMs));
      const bot = state.chatbots.find((c) => c.id === id);
      if (method === "POST") bot.share_token = "tok-" + id;
      if (method === "DELETE") bot.share_token = null;
      const t = bot.share_token;
      return json(200, {
        token: t, url: t ? `https://host/s/${t}` : null,
        embed: t ? `<iframe src="https://host/s/${t}"></iframe>` : null,
        daily_limit: 100, used_today: 0,
      });
    }
    return json(404, { detail: "not found" });
  };
}

async function boot() {
  const vc = new VirtualConsole();
  const errors = [];
  vc.on("jsdomError", (e) => errors.push(String(e.message || e)));

  let html = readFileSync(`${FE}/index.html`, "utf8").replace(/<script src="[^"]*"><\/script>/g, "");
  html = html.replace("</head>", `<style>${readFileSync(`${FE}/styles.css`, "utf8")}</style></head>`);
  const dom = new JSDOM(html, { runScripts: "dangerously", url: "https://host/", virtualConsole: vc });
  const w = dom.window, d = w.document;
  w.matchMedia = (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false });

  const state = {
    chatbots: [
      { id: "cb-a", name: "Alpha", description: "", share_token: "tok-cb-a" },
      { id: "cb-b", name: "Beta", description: "", share_token: null },
    ],
  };
  w.fetch = makeServer(state);
  w.localStorage.setItem("rag-chat-token", "t");
  w.localStorage.setItem("rag-chat-username", "oscar");
  w.confirm = () => true;
  w.prompt = () => null;
  Object.defineProperty(w.navigator, "clipboard", {
    value: { writeText: async (v) => { clipboard.written.push(v); } },
    configurable: true,
  });

  for (const f of ["theme.js", "markdown.js", "agents.js", "knowledge.js", "scope.js", "chatbots.js", "onboarding.js", "dashboard.js", "app.js"]) {
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

console.log("\n=== the reported bug: copy during the modal's load window ===");
{
  const { w, d } = await boot();
  slowMs = 60;                              // a real network round trip
  const cards = $$(d, ".bot-card:not(.bot-card--new)");
  const alpha = cards.find((c) => c.textContent.includes("Alpha"));

  click(w, alpha.querySelector(".bot-card__menu"));
  await flush(3);
  click(w, alpha.querySelector(".bot-card__actions button"));   // Share
  await flush(3);                                                // modal open, fetch in flight

  check(!$(d, "#share-modal").hidden, "the modal is open");

  clipboard.written = [];
  const copyBtn = $(d, "#share-copy");
  click(w, copyBtn);
  await flush(3);

  const copied = clipboard.written[0];
  const said = copyBtn.textContent;
  check(copied === undefined || copied !== "",
        "clicking Copy before the link loads must not copy an empty value",
        `copied ${JSON.stringify(copied)}`);
  check(!/copied/i.test(said) || (copied && copied !== ""),
        "the button must not claim success when nothing was copied",
        `button said ${JSON.stringify(said)}`);

  await flush(60);
  slowMs = 0;
}

console.log("\n=== stale value from the previously-opened chatbot ===");
{
  const { w, d } = await boot();
  const cards = $$(d, ".bot-card:not(.bot-card--new)");
  const alpha = cards.find((c) => c.textContent.includes("Alpha"));
  const beta = cards.find((c) => c.textContent.includes("Beta"));

  click(w, alpha.querySelector(".bot-card__menu")); await flush(3);
  click(w, alpha.querySelector(".bot-card__actions button")); await flush(30);
  const alphaUrl = $(d, "#share-url").value;
  click(w, $(d, "#share-close")); await flush(3);

  slowMs = 60;
  click(w, beta.querySelector(".bot-card__menu")); await flush(3);
  click(w, beta.querySelector(".bot-card__actions button")); await flush(3);

  clipboard.written = [];
  click(w, $(d, "#share-copy"));
  await flush(3);
  const copied = clipboard.written[0];
  check(copied !== alphaUrl,
        "opening Beta must never let you copy Alpha's link",
        `copied ${JSON.stringify(copied)} (Alpha's was ${JSON.stringify(alphaUrl)})`);
  await flush(60);
  slowMs = 0;
}

console.log("\n=== regenerating a LIVE link must be confirmed ===");
{
  const { w, d } = await boot();
  const cards = $$(d, ".bot-card:not(.bot-card--new)");
  const alpha = cards.find((c) => c.textContent.includes("Alpha"));
  click(w, alpha.querySelector(".bot-card__menu")); await flush(3);
  click(w, alpha.querySelector(".bot-card__actions button")); await flush(30);

  let asked = false;
  w.confirm = () => { asked = true; return false; };
  click(w, $(d, "#share-regenerate"));
  await flush(20);
  check(asked, "regenerating an already-shared link asks first",
        "it silently invalidates a link you may already have sent");
}

const bad = results.filter((r) => !r.ok);
console.log("\n" + "=".repeat(66));
console.log(`${results.length} checks, ${results.length - bad.length} passed, ${bad.length} FAILED`);
process.exit(bad.length ? 1 : 0);
