// The embed widget loader (widget.js) — zero coverage before this file.
//
// Unlike every other runner here, this one does NOT boot this project's own
// index.html. widget.js runs on a STRANGER'S page: a host site pastes one
// <script> tag and gets a shadow-rooted tab + iframe panel pointed at our
// public chat page. So this file builds a minimal host document from scratch
// and injects the widget the way a real site would.
//
// Two jsdom facts drove the plumbing below:
//
// 1. The loader reads `document.currentScript` synchronously at the top of
//    its IIFE, and needs `script.src` (to derive its own origin) at that same
//    instant. `currentScript` is only non-null while a classic script is
//    actually executing — `w.eval(source)` leaves it null and the loader
//    bails on its very first line. Assigning `textContent` and appending a
//    plain <script> (no `src` attribute) DOES execute with `currentScript`
//    set, but then `.src` reads back as "" (nothing to reflect), which is no
//    good either. The fix is jsdom 29's `requestInterceptor`: give the DOM a
//    `resources: { interceptors: [...] }` loader that answers a GET for
//    `https://widget.example/widget.js` with this repo's real widget.js
//    source. That makes it a genuinely fetched EXTERNAL script, so jsdom runs
//    it with `currentScript.src` correctly resolved to the widget's own
//    origin — distinct from the host page's origin, which is exactly what
//    the origin-check tests (9/10/11) need.
//
// 2. That same interceptor option flips on subresource loading for the whole
//    window, which means the `<script src=".../widget.css.js">` the loader
//    injects into <head> ALSO really fetches — through the same interceptor,
//    serving this repo's real widget.css.js source, so `cssTag.onload` fires
//    for real and the widget applies its actual styles. That used to be
//    unsafe: widget.css.js declared `widgetVars`/`WIDGET_CSS` as bare
//    top-level names, so loading it for real leaked a second global onto the
//    HOST window — a fix-round-1 finding, now fixed at the source: the file
//    is wrapped in its own IIFE and publishes one namespaced object,
//    `window.__powabaseWidgetCSS`. So check 2 below can now assert the
//    loader's whole real guarantee — exactly two names on window, both
//    namespaced, nothing else — instead of a version narrowed to dodge a
//    leak that no longer exists.

import pkg from "jsdom";
const { JSDOM, requestInterceptor } = pkg;
import { readFileSync } from "fs";

const FE = "/Users/oscar/Downloads/rag-chatbot/frontend";
const WIDGET_JS = readFileSync(`${FE}/widget.js`, "utf8");
const WIDGET_CSS_JS = readFileSync(`${FE}/widget.css.js`, "utf8");

// Two distinct fake origins: the host site the widget is pasted onto, and the
// origin the widget script itself is served from. Keeping them different is
// what makes checks 9/10/11 (the message-origin guard) meaningful at all.
const WIDGET_ORIGIN = "https://widget.example";
const HOST_URL = "https://host.example/page";

const results = [];
function check(ok, label, detail = "") {
  results.push({ ok, label, detail });
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
}
const flush = async (n = 15) => {
  for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0));
};

// ---------- the fake backend ----------
// Records every call; answers the session lifecycle the loader drives:
//   GET  /s/:token/session/:id/messages   — "does this stored id still exist?"
//   POST /s/:token/session                — "make me a new one"
function makeFetch(calls, { validateStatus = 200 } = {}) {
  let n = 0;
  return async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    calls.push(`${method} ${url}`);
    const json = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
    if (/\/session$/.test(String(url)) && method === "POST") {
      n += 1;
      return json(200, { session_id: `new-session-${n}` });
    }
    if (/\/session\/[^/]+\/messages$/.test(String(url)) && method === "GET") {
      return json(validateStatus, {});
    }
    return json(404, { detail: "not found" });
  };
}

function makeDom() {
  const interceptor = requestInterceptor(async (request) => {
    if (request.url === `${WIDGET_ORIGIN}/widget.js`) {
      return new Response(WIDGET_JS, { headers: { "Content-Type": "application/javascript" } });
    }
    if (request.url === `${WIDGET_ORIGIN}/widget.css.js`) {
      return new Response(WIDGET_CSS_JS, { headers: { "Content-Type": "application/javascript" } });
    }
    // Anything else: stay pending forever rather than hit real network.
    return new Promise(() => {});
  });
  return new JSDOM("<!doctype html><html><body></body></html>", {
    runScripts: "dangerously",
    url: HOST_URL,
    pretendToBeVisual: true,
    resources: { interceptors: [interceptor] },
  });
}

// Pastes the <script> tag the way a host site's HTML would. Attributes are
// set before insertion, same as a literal <script data-token="…"> in markup —
// they must already be there the instant the loader's IIFE reads them.
function paste(d, { token = "tok1", side, label, accent } = {}) {
  const el = d.createElement("script");
  el.setAttribute("data-token", token);
  if (side) el.setAttribute("data-side", side);
  if (label) el.setAttribute("data-label", label);
  if (accent) el.setAttribute("data-accent", accent);
  el.setAttribute("src", `${WIDGET_ORIGIN}/widget.js`);
  d.body.appendChild(el);
  return el;
}

// Boots a fresh host document, pastes the widget once, and waits for it to
// settle. `presetOpen`/`presetSession` seed localStorage before the paste, so
// they are in place the instant the loader's IIFE runs — the same as a
// returning visitor's browser already holding that state.
async function boot({ token = "tok1", validateStatus, presetOpen, presetSession, throwStorage } = {}) {
  const dom = makeDom();
  const w = dom.window;
  const d = w.document;
  const calls = [];
  w.fetch = makeFetch(calls, { validateStatus });

  if (throwStorage) {
    // A host page can block site data entirely. Replace localStorage itself
    // (assigning to instance methods on jsdom's Storage is a no-op — it is
    // proxy-backed and treats `localStorage.getItem = fn` as SETTING an item
    // named "getItem", not overriding the method) with an object whose every
    // operation throws, so the loader's own try/catch wrappers are what's
    // actually under test.
    Object.defineProperty(w, "localStorage", {
      configurable: true,
      value: {
        getItem() { throw new Error("storage blocked"); },
        setItem() { throw new Error("storage blocked"); },
        removeItem() { throw new Error("storage blocked"); },
      },
    });
  } else {
    if (presetOpen) w.localStorage.setItem(`powabase-widget:${token}:open`, "1");
    if (presetSession) w.localStorage.setItem(`powabase-widget:${token}:session`, presetSession);
  }

  // Snapshot AFTER our own setup (fetch stub) so the leak check (2) only
  // catches what widget.js itself adds, not this harness's own scaffolding.
  const beforeKeys = new Set(Object.keys(w));

  paste(d, { token });
  await flush();

  const leaked = Object.keys(w).filter((k) => !beforeKeys.has(k));
  const host = d.querySelector("[data-powabase-widget]");
  const shadow = host && host.shadowRoot;
  return {
    w, d, host, shadow, calls, token, leaked,
    tab: shadow && shadow.querySelector(".tab"),
    wrap: shadow && shadow.querySelector(".wrap"),
    frame: shadow && shadow.querySelector("iframe"),
  };
}

const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const postCalls = (calls) => calls.filter((c) => c.startsWith("POST") && c.includes("/session") && !c.includes("/messages"));

console.log("\n=== the embed widget loader ===");

// 1. Shadow root built; tab is inside it, not reachable from the host document.
{
  const { d, host, shadow, tab } = await boot();
  check(!!host && !!shadow && !!tab && d.querySelector(".tab") === null,
        "1. a shadow root is built and the tab lives inside it, not the host document",
        `host=${!!host} shadow=${!!shadow} tab=${!!tab} leaked-.tab=${!!d.querySelector(".tab")}`);
}

// 2. Nothing leaks onto the host page. The CSS companion script loads for
// real here (makeDom's interceptor serves it), so this proves the loader's
// full guarantee — including the styles it injects — not a version narrowed
// to dodge a leak that widget.css.js no longer has.
{
  const { d, leaked } = await boot();
  const sortedLeaked = [...leaked].sort();
  check(d.querySelectorAll("style").length === 0
        && sortedLeaked.length === 2
        && sortedLeaked[0] === "__powabaseWidget"
        && sortedLeaked[1] === "__powabaseWidgetCSS",
        "2. nothing leaks onto the host page — no <style>, and window gains only __powabaseWidget and __powabaseWidgetCSS",
        `styles=${d.querySelectorAll("style").length} newKeys=${JSON.stringify(sortedLeaked)}`);
}

// 3. No session on page load.
{
  const { calls } = await boot();
  check(postCalls(calls).length === 0,
        "3. no session is created on page load, before any click",
        `calls=${JSON.stringify(calls)}`);
}

// 4. First open creates exactly one session.
{
  const { w, tab, frame, calls } = await boot();
  click(w, tab);
  await flush();
  check(postCalls(calls).length === 1 && frame.src.includes("#session="),
        "4. first open creates exactly one session and puts it in the fragment",
        `posts=${postCalls(calls).length} frame.src=${frame.src}`);
}

// 5. Second open reuses it.
{
  const { w, tab, calls } = await boot();
  click(w, tab); // open  -> creates
  await flush();
  click(w, tab); // close
  await flush();
  click(w, tab); // open again -> should reuse
  await flush();
  check(postCalls(calls).length === 1,
        "5. reopening reuses the existing session — still exactly one POST",
        `posts=${postCalls(calls).length}`);
}

// 6. The tab toggles data-open, and aria-expanded tracks it.
{
  const { w, tab, wrap } = await boot();
  click(w, tab);
  await flush();
  const openState = wrap.hasAttribute("data-open");
  const expandedOpen = tab.getAttribute("aria-expanded");
  click(w, tab);
  await flush();
  const closedState = wrap.hasAttribute("data-open");
  const expandedClosed = tab.getAttribute("aria-expanded");
  check(openState === true && expandedOpen === "true" && closedState === false && expandedClosed === "false",
        "6. the tab toggles data-open and aria-expanded tracks it",
        `open=${openState}/${expandedOpen} closed=${closedState}/${expandedClosed}`);
}

// 7. Open state persists across a boot.
{
  const { wrap } = await boot({ presetOpen: true });
  check(wrap.hasAttribute("data-open"),
        "7. with the open key pre-set, the widget boots open",
        `data-open=${wrap.hasAttribute("data-open")}`);
}

// 8. Session id persists when the stored id still validates.
{
  const { w, tab, frame, calls } = await boot({ presetSession: "stored-1", validateStatus: 200 });
  click(w, tab);
  await flush();
  check(postCalls(calls).length === 0 && frame.src.includes("#session=stored-1"),
        "8. a stored session that still validates is reused with no POST",
        `posts=${postCalls(calls).length} frame.src=${frame.src}`);
}

// 8b. A dead stored id is replaced, not handed over.
{
  const { w, d, tab, frame, calls, token } = await boot({ presetSession: "dead-1", validateStatus: 404 });
  click(w, tab);
  await flush();
  const stored = d.defaultView.localStorage.getItem(`powabase-widget:${token}:session`);
  check(postCalls(calls).length === 1
        && frame.src.includes("#session=") && !frame.src.includes("dead-1")
        && stored !== "dead-1" && stored !== null,
        "8b. a dead stored id triggers one POST, a new id in the fragment, and an updated store",
        `posts=${postCalls(calls).length} frame.src=${frame.src} stored=${stored}`);
}

// 9. A close message from the right origin closes the panel.
{
  const { w, tab, wrap } = await boot();
  click(w, tab);
  await flush();
  w.dispatchEvent(new w.MessageEvent("message", {
    origin: WIDGET_ORIGIN, data: { source: "powabase-widget", type: "close" },
  }));
  await flush();
  check(wrap.hasAttribute("data-open") === false,
        "9. a close message from the right origin closes the panel",
        `data-open=${wrap.hasAttribute("data-open")}`);
}

// 10. A close message from the wrong origin is ignored.
{
  const { w, tab, wrap } = await boot();
  click(w, tab);
  await flush();
  w.dispatchEvent(new w.MessageEvent("message", {
    origin: "https://evil.example", data: { source: "powabase-widget", type: "close" },
  }));
  await flush();
  check(wrap.hasAttribute("data-open") === true,
        "10. a close message from the WRONG origin is ignored — panel stays open",
        `data-open=${wrap.hasAttribute("data-open")}`);
}

// 11. A right-origin message missing the source field is ignored.
{
  const { w, tab, wrap } = await boot();
  click(w, tab);
  await flush();
  w.dispatchEvent(new w.MessageEvent("message", {
    origin: WIDGET_ORIGIN, data: { type: "close" },
  }));
  await flush();
  check(wrap.hasAttribute("data-open") === true,
        "11. a right-origin message without source: \"powabase-widget\" is ignored",
        `data-open=${wrap.hasAttribute("data-open")}`);
}

// 12. Throwing storage still yields a working widget.
{
  const { w, tab, wrap } = await boot({ throwStorage: true });
  const renders = !!tab;
  click(w, tab);
  await flush();
  const opened = wrap.hasAttribute("data-open");
  check(renders && opened === true,
        "12. throwing storage still yields a working widget — tab renders and toggles",
        `renders=${renders} opened=${opened}`);
}

// 13. Pasting the script twice builds exactly one widget.
{
  const dom = makeDom();
  const w = dom.window, d = w.document;
  w.fetch = makeFetch([]);
  paste(d);
  await flush();
  paste(d);
  await flush();
  check(d.querySelectorAll("[data-powabase-widget]").length === 1,
        "13. pasting the script twice builds one widget",
        `count=${d.querySelectorAll("[data-powabase-widget]").length}`);
}

// 14. The launcher is a glyph, not a word. It is a 52px circle in the corner
// with no room for text, so its accessible name has to come from aria-label —
// otherwise a screen reader announces an empty button. Both glyphs live in the
// button at once and CSS shows whichever the open state calls for, so toggling
// never rebuilds DOM inside somebody else's page.
{
  const dom = makeDom();
  const w = dom.window, d = w.document;
  w.fetch = makeFetch([]);
  paste(d);
  await flush();
  const tab = d.querySelector("[data-powabase-widget]").shadowRoot.querySelector(".tab");
  const svgs = [...tab.querySelectorAll("svg")];
  const ns = "http://www.w3.org/2000/svg";

  check(tab.textContent.trim() === ""
        && (tab.getAttribute("aria-label") || "").length > 0
        && svgs.length === 2
        && svgs.every((el) => el.namespaceURI === ns)
        && svgs.every((el) => el.getAttribute("aria-hidden") === "true"),
        "14. the launcher is an aria-labelled button holding two hidden glyphs",
        `text="${tab.textContent.trim()}" label="${tab.getAttribute("aria-label")}" svgs=${svgs.length}`);
}

// 15. The glyph swap is pure CSS, so jsdom cannot observe it — it has no
// cascade for this and getComputedStyle reports nothing useful. Read the
// stylesheet statically instead, the way run.mjs Section L audits the [hidden]
// cascade. Without these rules the button shows both glyphs at once, and no
// runtime assertion here would ever notice.
{
  const css = readFileSync(`${FE}/widget.css.js`, "utf8");
  const hidesChat = /\.wrap\[data-open\]\s+\.tab\s+\.ico-chat\s*\{[^}]*display:\s*none/.test(css);
  const showsClose = /\.wrap\[data-open\]\s+\.tab\s+\.ico-close\s*\{[^}]*display:\s*block/.test(css);
  const closeHiddenByDefault = /\.tab\s+\.ico-close\s*\{[^}]*display:\s*none/.test(css);

  check(hidesChat && showsClose && closeHiddenByDefault,
        "15. the stylesheet swaps the glyph on open, and hides close by default",
        `hidesChat=${hidesChat} showsClose=${showsClose} closeDefault=${closeHiddenByDefault}`);
}

// 16. `reset` discards the stored session and starts a clean one. It joins
// `close` and `ready` as the third message the panel may send, and it has to be
// the LOADER that acts on it: the loader holds the stored id, so a panel that
// reset itself would leave that id behind and the two would disagree about
// which conversation is current — the split ownership this design exists to
// prevent. Carries no secret, which is why it is safe to send over postMessage
// at all.
{
  const dom = makeDom();
  const w = dom.window, d = w.document;
  const calls = [];
  w.fetch = makeFetch(calls);
  w.localStorage.setItem("powabase-widget:tok1:session", "old-session");
  paste(d);
  await flush();

  const root = d.querySelector("[data-powabase-widget]").shadowRoot;
  root.querySelector(".tab").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush();
  const before = root.querySelector("iframe").src;

  w.dispatchEvent(new w.MessageEvent("message", {
    origin: WIDGET_ORIGIN, data: { source: "powabase-widget", type: "reset" },
  }));
  await flush();
  const after = root.querySelector("iframe").src;
  const stored = w.localStorage.getItem("powabase-widget:tok1:session");

  check(before.includes("old-session")
        && !after.includes("old-session")
        && stored !== "old-session",
        "16. reset discards the stored session and loads a fresh one",
        `before=${before.slice(-24)} after=${after.slice(-24)} stored=${stored}`);
}

// 16b. Reset drops the stored id BEFORE trying to make a new one, so a failure
// leaves no stale id behind. Without that ordering the discard is invisible —
// a successful reset overwrites the key anyway — and the only case that can
// tell the difference is this one: the visitor asked to start over, the new
// session could not be created, and the next page load must not silently
// resume the conversation they just discarded.
{
  const dom = makeDom();
  const w = dom.window, d = w.document;
  w.fetch = makeFetch([]);
  w.localStorage.setItem("powabase-widget:tok1:session", "old-session");
  paste(d);
  await flush();
  const root = d.querySelector("[data-powabase-widget]").shadowRoot;
  root.querySelector(".tab").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await flush();

  // Every request fails from here on: the reset cannot mint a replacement.
  w.fetch = async () => ({ ok: false, status: 500, json: async () => ({}) });
  w.dispatchEvent(new w.MessageEvent("message", {
    origin: WIDGET_ORIGIN, data: { source: "powabase-widget", type: "reset" },
  }));
  await flush();

  const stored = w.localStorage.getItem("powabase-widget:tok1:session");
  check(stored === null,
        "16b. a reset that cannot mint a new session leaves no stale id",
        `stored=${stored}`);
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
