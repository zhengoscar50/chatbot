// The embeddable chat widget.
//
// A host site pastes one tag:
//   <script src="https://…/widget.js" data-token="…" async></script>
//
// This file runs on THEIR page, so it assumes nothing: no other script of ours
// is loaded, no CSS of ours exists, and their globals are none of our business.
// It declares exactly one name on window, as a guard against being pasted twice.
//
// Everything it builds lives inside an open shadow root, so their stylesheet
// cannot break the tab and the tab's styles cannot reach their layout. The chat
// itself stays in an iframe on our origin — model output and document text
// should never render inside a stranger's document.

(function () {
  if (window.__powabaseWidget) return;
  window.__powabaseWidget = true;

  // Must be read synchronously: currentScript is null once we are in a callback.
  const script = document.currentScript;
  if (!script) return;
  const origin = new URL(script.src, location.href).origin;
  const token = script.getAttribute("data-token") || "";
  const side = script.getAttribute("data-side") === "left" ? "left" : "right";
  const label = script.getAttribute("data-label") || "Chat with us";
  const accent = script.getAttribute("data-accent") || "#3e6ae1";
  if (!token) return;

  const KEY_SESSION = `powabase-widget:${token}:session`;
  const KEY_OPEN = `powabase-widget:${token}:open`;

  // A host page may block site data entirely. That must degrade to a widget
  // that works and forgets, never to a widget that fails to appear.
  function read(key) {
    try { return localStorage.getItem(key); } catch (err) { return null; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (err) { /* forget instead */ }
  }
  function drop(key) {
    try { localStorage.removeItem(key); } catch (err) { /* nothing to do */ }
  }

  let sessionId = read(KEY_SESSION);
  let open = false;
  let loaded = false;
  let loading = false;

  // One <svg> per state. Both live in the button and CSS shows whichever the
  // open state calls for, so toggling never rebuilds DOM on someone's page.
  function tabIcon(cls, d) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", cls);
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
    return svg;
  }

  const host = document.createElement("div");
  host.setAttribute("data-powabase-widget", "");
  // Nothing is shown until the stylesheet lands: an unstyled tab in a
  // stranger's layout for even a moment is worse than a slightly later one.
  host.style.display = "none";
  const root = host.attachShadow({ mode: "open" });

  const wrap = document.createElement("div");
  wrap.className = "wrap";
  wrap.setAttribute("data-side", side);

  const tab = document.createElement("button");
  tab.type = "button";
  tab.className = "tab";
  // A glyph, not text: the launcher is a 52px circle in the corner and there is
  // no room for a word in it. `data-label` becomes the accessible name instead,
  // so the button still announces itself to a screen reader.
  //
  // Built with createElementNS and appendChild rather than innerHTML — this runs
  // inside somebody else's page, and a widget that assembles markup from a
  // string is one careless change away from doing it with a value it was given.
  tab.appendChild(tabIcon(
    "ico-chat",
    "M21 11.5a8.4 8.4 0 0 1-8.5 8.3 8.8 8.8 0 0 1-3.9-.9L3 21l1.9-5.4A8.2 8.2 0 0 1 4 11.5 8.4 8.4 0 0 1 12.5 3 8.4 8.4 0 0 1 21 11.5z"
  ));
  tab.appendChild(tabIcon("ico-close", "M18 6 6 18M6 6l12 12"));
  tab.setAttribute("aria-label", label);
  tab.setAttribute("aria-expanded", "false");

  const panel = document.createElement("div");
  panel.className = "panel";
  const frame = document.createElement("iframe");
  frame.title = "Chat";
  panel.appendChild(frame);

  wrap.appendChild(tab);
  wrap.appendChild(panel);

  // Styles come from our own origin so the host pastes only one tag, and so
  // widget.css.js stays the single place the styles live.
  const cssTag = document.createElement("script");
  cssTag.src = origin + "/widget.css.js";
  cssTag.onload = function () {
    const bundle = window.__powabaseWidgetCSS;
    if (!bundle) { host.remove(); return; }
    const style = document.createElement("style");
    style.textContent = bundle.vars(accent) + bundle.css;
    root.appendChild(style);
    host.style.display = "";
  };
  cssTag.onerror = function () { host.remove(); };
  document.head.appendChild(cssTag);

  root.appendChild(wrap);

  // The session is created on FIRST OPEN, never on page load: a visitor who
  // never clicks should leave no empty conversation in the owner's data.
  async function ensureSession() {
    // A stored id can outlive the session it names. Check before handing it
    // over, so the panel can trust what it is given absolutely and never has
    // to invent one of its own — two things creating sessions is exactly the
    // split ownership this design exists to avoid.
    if (sessionId) {
      const check = await fetch(
        `${origin}/s/${encodeURIComponent(token)}/session/`
        + `${encodeURIComponent(sessionId)}/messages`
      );
      if (check.ok) return sessionId;
      sessionId = null;
      drop(KEY_SESSION);
    }
    const res = await fetch(`${origin}/s/${encodeURIComponent(token)}/session`, {
      method: "POST",
    });
    if (!res.ok) return null;
    sessionId = (await res.json()).session_id;
    write(KEY_SESSION, sessionId);
    return sessionId;
  }

  async function load() {
    if (loaded || loading) return;
    loading = true;
    // The id travels in the fragment, never through postMessage: a fragment
    // goes only to the frame we are pointing at, whereas a message would be
    // posted to a parent whose origin the panel cannot verify.
    const base = `${origin}/s/${encodeURIComponent(token)}?embed=1`;
    try {
      const id = await ensureSession();
      // Load the page either way, so a failure shows the chat's own error
      // rather than a blank white rectangle. But only a run that actually got
      // a session counts as loaded — otherwise a single blip would leave the
      // widget dead for the rest of the visit.
      frame.src = id ? `${base}#session=${encodeURIComponent(id)}` : base;
      if (id) loaded = true;
    } catch (err) {
      // A network failure must not become an unhandled rejection in the
      // HOST page's console, and must not leave the panel blank. `loaded`
      // stays false so the next open retries; the frame still points at the
      // base URL so the visitor sees the chat's own error instead.
      frame.src = base;
    } finally {
      loading = false;
    }
  }

  // Throw the conversation away and start a clean one. The loader does this,
  // not the panel, because the loader is what holds the stored id — a panel
  // that reset itself would leave that id behind and the two would disagree
  // about which conversation is current.
  //
  // The old conversation is not deleted from the owner's data; the visitor
  // simply gets a fresh one. Saying otherwise would promise an erasure this
  // cannot deliver.
  async function resetConversation() {
    sessionId = null;
    drop(KEY_SESSION);
    loaded = false;
    frame.src = "about:blank";   // drop the old transcript before the new load
    await load();
  }

  function setOpen(next) {
    open = next;
    if (open) wrap.setAttribute("data-open", "");
    else wrap.removeAttribute("data-open");
    tab.setAttribute("aria-expanded", open ? "true" : "false");
    write(KEY_OPEN, open ? "1" : "0");
    if (open) load();
  }

  tab.addEventListener("click", function () { setOpen(!open); });

  window.addEventListener("message", function (event) {
    // Both checks matter. Origin alone still lets another widget on the same
    // host talk to us; the source field alone lets any page talk to us.
    if (event.origin !== origin) return;
    const data = event.data;
    if (!data || data.source !== "powabase-widget") return;
    if (data.type === "close") setOpen(false);
    if (data.type === "reset") resetConversation();
  });

  // async on the script tag means "don't block the parser", not "wait for
  // body" — a host pasting the tag in <head> can run this before <body>
  // exists, so defer mounting until it does.
  function mount() {
    document.body.appendChild(host);
    if (read(KEY_OPEN) === "1") setOpen(true);
  }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
