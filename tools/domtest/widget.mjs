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
//    injects into <head> would ALSO really fetch. Two ways that goes wrong:
//    serving it a 404 fires the loader's onerror, which calls host.remove()
//    and deletes the widget outright; serving the real file makes its bare
//    top-level `function widgetVars(){}` leak onto the HOST window (browsers
//    do this too — it is a real, if minor, quirk of loading a second classic
//    script into the same global scope) — which would make check 2 fail for
//    a reason that has nothing to do with widget.js's own guard. So the
//    interceptor answers everything except widget.js with a promise that
//    never resolves: the CSS <script> stays perpetually in flight, its
//    onload/onerror never fire, `host.style.display` stays "none" forever,
//    and nothing new lands on `window`. That matches the brief's own note
//    that "jsdom will not load the CSS script" — checks never assert on
//    visibility (`data-open` and `frame.src` only, never getComputedStyle),
//    so an invisible-but-present host is exactly as testable as a styled one.

import pkg from "jsdom";
const { JSDOM, requestInterceptor } = pkg;
import { readFileSync } from "fs";

const FE = "/Users/oscar/Downloads/rag-chatbot/frontend";
const WIDGET_JS = readFileSync(`${FE}/widget.js`, "utf8");

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
    // widget.css.js and anything else: stay pending forever. See file header.
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

// 2. Nothing leaks onto the host page.
{
  const { d, leaked } = await boot();
  check(d.querySelectorAll("style").length === 0 && leaked.length === 1 && leaked[0] === "__powabaseWidget",
        "2. nothing leaks onto the host page — no <style>, only __powabaseWidget added to window",
        `styles=${d.querySelectorAll("style").length} newKeys=${JSON.stringify(leaked)}`);
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

// =====================================================================
const bad = results.filter((r) => !r.ok);
console.log("\n" + "=".repeat(72));
console.log(`${results.length} checks, ${results.length - bad.length} passed, ${bad.length} FAILED`);
if (bad.length) {
  console.log("\nFAILURES:");
  bad.forEach((r) => console.log(`  ${r.label}${r.detail ? "  — " + r.detail : ""}`));
}
process.exit(bad.length ? 1 : 0);
