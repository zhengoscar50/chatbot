// The widget in a real browser, on a real other origin.
//
// Everything in tools/domtest runs under jsdom, which has no layout engine and
// no real cross-origin model. That leaves the widget's central claim untested:
// that a host page's CSS cannot reach inside it. The shadow root exists for
// exactly that, and only a rendering engine can say whether it held.
//
// This drives the installed Chrome against a deliberately hostile host page
// that embeds the DEPLOYED widget — a different scheme, host and port, which
// is a real origin split rather than a same-machine stand-in. It never starts
// a local backend, because this project's local and deployed environments
// share one Powabase project and booting the app locally rewrites the live
// demo's orchestrator.
//
//   WIDGET_BASE=https://… WIDGET_TOKEN=… node widget-browser.mjs
//
// Sending a chat message is opt-in (CHAT=1): it spends one of the share
// link's daily allowance against the real chatbot.

import http from "http";
import puppeteer from "puppeteer-core";

const BASE = process.env.WIDGET_BASE;
const TOKEN = process.env.WIDGET_TOKEN;
const CHROME = process.env.CHROME_PATH
  || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = Number(process.env.HOST_PORT || 4173);
const DO_CHAT = process.env.CHAT === "1";

if (!BASE || !TOKEN) {
  console.error("Set WIDGET_BASE and WIDGET_TOKEN.");
  process.exit(2);
}

const results = [];
const check = (ok, label, detail = "") => {
  results.push({ ok, label });
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
};
const skip = (label, why) => {
  results.push({ ok: true, skipped: true, label });
  console.log(`  [skip] ${label}  — ${why}`);
};

// The host page fights the widget the way a real site does by accident: every
// rule !important, every rule the kind a CSS framework ships.
const page = (title) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${title}</title>
<style>
  * { box-sizing: content-box !important; }
  div { margin: 12px !important; }
  button { all: unset !important; font-size: 30px !important; }
  iframe { border: 6px dashed red !important; }
  body { margin: 0; background: #fff; font-family: serif; }
</style></head>
<body><h1>${title}</h1>
<script src="${BASE}/widget.js" data-token="${TOKEN}" data-label="Chat with us" async><\/script>
</body></html>`;

const server = http.createServer((req, res) => {
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  res.end(page(req.url === "/second.html" ? "Second page" : "Host page"));
});
await new Promise((r) => server.listen(PORT, r));
const HOST = `http://localhost:${PORT}`;

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

// Reach inside the widget's shadow root. The host page cannot do this; the
// automation driver can, which is what lets us measure what a visitor sees.
const inShadow = (p, fn, ...args) => p.evaluate(new Function("fn", "args", `
  const host = document.querySelector("#powabase-widget, [data-powabase-widget]")
    || [...document.body.children].find((el) => el.shadowRoot);
  if (!host || !host.shadowRoot) return { error: "no shadow host" };
  return (0, eval)("(" + fn + ")")(host.shadowRoot, ...args);
`), fn.toString(), args);

async function openPage(url = HOST, viewport = { width: 1280, height: 900 }) {
  const p = await browser.newPage();
  await p.setViewport(viewport);
  await p.goto(url, { waitUntil: "networkidle2", timeout: 30000 });
  await p.waitForFunction(
    () => [...document.body.children].some((el) => el.shadowRoot),
    { timeout: 15000 },
  );
  return p;
}

console.log(`\n=== the widget loads on a genuinely different origin (${HOST} -> ${BASE}) ===`);
let p;
try {
  p = await openPage();
  check(true, "the widget mounts a shadow root on the host page");
} catch (e) {
  check(false, "the widget mounts a shadow root on the host page", String(e.message).slice(0, 120));
  await browser.close(); server.close();
  process.exit(1);
}

console.log("\n=== hostile host CSS does not reach inside ===");
{
  // The launcher. `button { all: unset !important; font-size: 30px }` on the
  // host would flatten it if the shadow boundary leaked.
  const launcher = await inShadow(p, (root) => {
    const b = root.querySelector("button");
    if (!b) return { error: "no button" };
    const r = b.getBoundingClientRect();
    const cs = getComputedStyle(b);
    return { w: r.width, h: r.height, right: innerWidth - r.right,
             bottom: innerHeight - r.bottom, fontSize: cs.fontSize };
  });
  check(!launcher.error, "the launcher exists inside the shadow root", launcher.error || "");
  check(launcher.h >= 40 && launcher.h <= 80,
        "the launcher keeps its own size, not the host's 30px button rule",
        `${Math.round(launcher.w)}x${Math.round(launcher.h)}`);
  check(launcher.right >= 0 && launcher.right < 60 && launcher.bottom < 60,
        "it sits in the bottom-right corner",
        `right:${Math.round(launcher.right)} bottom:${Math.round(launcher.bottom)}`);
}

console.log("\n=== opening the panel ===");
{
  await inShadow(p, (root) => root.querySelector("button").click());
  await new Promise((r) => setTimeout(r, 1200));

  const panel = await inShadow(p, (root) => {
    const f = root.querySelector("iframe");
    if (!f) return { error: "no iframe" };
    const r = f.getBoundingClientRect();
    const cs = getComputedStyle(f);
    return { w: r.width, h: r.height, visible: r.width > 0 && r.height > 0,
             border: cs.borderTopWidth + " " + cs.borderTopStyle + " " + cs.borderTopColor };
  });
  check(!panel.error && panel.visible, "the panel opens to a real size",
        panel.error || `${Math.round(panel.w)}x${Math.round(panel.h)}`);

  // THE shadow-boundary check. The host sets `iframe { border: 6px dashed red
  // !important }`; if that reaches the widget's iframe, the boundary leaked.
  const leaked = /red|rgb\(255, 0, 0\)/.test(panel.border || "")
                 || /dashed/.test(panel.border || "");
  check(!leaked, "the host's `iframe { border: 6px dashed red !important }` did NOT reach it",
        panel.border);
}

console.log("\n=== the conversation inside the panel ===");
{
  const frame = p.frames().find((f) => f.url().includes("/s/"));
  check(!!frame, "the panel is a cross-origin frame pointing at the share page",
        frame ? new URL(frame.url()).origin : "none");

  if (frame) {
    // The share page has no <form>: it is an #q input beside a #send button,
    // so the message is sent by clicking, not by submitting.
    const hasComposer = await frame.evaluate(
      () => !!document.querySelector("#q") && !!document.querySelector("#send"));
    check(hasComposer, "the chat page rendered its composer inside the frame");

    if (DO_CHAT) {
      // The share page holds a `busy` flag across its whole boot and only
      // clears it after replaying the transcript, so no DOM signal marks the
      // moment it will accept input. Rather than proxy for that with a delay
      // that races, offer the message until it is taken: send() adds the
      // visitor's own bubble as its first act, so that bubble appearing is
      // proof the call actually ran rather than returning early.
      const before = await frame.evaluate(
        () => document.querySelectorAll(".bubble--assistant").length);

      let accepted = false;
      for (let attempt = 0; attempt < 20 && !accepted; attempt += 1) {
        await frame.evaluate((text) => {
          const i = document.getElementById("q");
          i.value = text;
          i.dispatchEvent(new Event("input", { bubbles: true }));
          // Dispatched in-frame, not via frame.click: that computes viewport
          // coordinates, and this iframe is cross-origin AND inside a shadow
          // root, where the maths does not land on the element.
          document.getElementById("send").click();
        }, "Hello from the automated browser check");
        accepted = await frame.evaluate(
          () => document.querySelectorAll(".bubble--user").length > 0);
        if (!accepted) await new Promise((r) => setTimeout(r, 500));
      }
      check(accepted, "the page accepts the message once its boot finishes");

      const answered = accepted && await frame.waitForFunction(
        (n) => document.querySelectorAll(".bubble--assistant").length > n,
        { timeout: 90000 }, before).then(() => true).catch(() => false);
      if (!answered) {
        const state = await frame.evaluate(() => ({
          bubbles: document.querySelectorAll(".bubble").length,
          value: document.getElementById("q")?.value,
        }));
        console.log("        state:", JSON.stringify(state).slice(0, 200));
      }
      check(answered, "sending a message produces an answer");
      if (answered) {
        const text = await frame.evaluate(
          () => [...document.querySelectorAll(".bubble--assistant")].pop()?.textContent?.trim() || "");
        check(text.length > 0, "and the answer has content", text.slice(0, 70) + "…");
      }
    } else {
      skip("sending a message produces an answer", "set CHAT=1 (spends the daily allowance)");
    }
  }
}

console.log("\n=== a narrow screen ===");
{
  const np = await openPage(HOST, { width: 380, height: 780 });
  await inShadow(np, (root) => root.querySelector("button").click());
  await new Promise((r) => setTimeout(r, 1200));
  const m = await inShadow(np, (root) => {
    const f = root.querySelector("iframe");
    const r = f.getBoundingClientRect();
    return { w: r.width, vw: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth };
  });
  check(m.w <= m.vw, "the panel fits the viewport rather than overflowing it",
        `panel ${Math.round(m.w)} vs viewport ${m.vw}`);
  check(!m.overflow, "and the host page does not scroll sideways because of it");
  await np.close();
}

console.log("\n=== both themes ===");
{
  for (const scheme of ["light", "dark"]) {
    const tp = await browser.newPage();
    await tp.emulateMediaFeatures([{ name: "prefers-color-scheme", value: scheme }]);
    await tp.setViewport({ width: 1280, height: 900 });
    await tp.goto(HOST, { waitUntil: "networkidle2", timeout: 30000 });
    await tp.waitForFunction(() => [...document.body.children].some((el) => el.shadowRoot),
                             { timeout: 15000 });
    const seen = await inShadow(tp, (root) => {
      const b = root.querySelector("button");
      const cs = getComputedStyle(b);
      return { bg: cs.backgroundColor, color: cs.color };
    });
    const transparent = /rgba\(0, 0, 0, 0\)|transparent/.test(seen.bg || "");
    check(!transparent, `in ${scheme}, the launcher paints its own background`,
          `${seen.bg} / ${seen.color}`);
    await tp.close();
  }
  skip("both themes are LEGIBLE", "contrast is measurable, but 'reads well' needs eyes");
}

console.log("\n=== the thread survives a reload and a navigation ===");
{
  const rp = await openPage();
  await inShadow(rp, (root) => root.querySelector("button").click());
  await new Promise((r) => setTimeout(r, 1500));
  const first = await rp.evaluate(() => {
    try { return Object.keys(localStorage).filter((k) => /powabase|widget/i.test(k))
      .map((k) => `${k}=${localStorage.getItem(k)}`).join("|"); } catch { return ""; }
  });
  check(first.length > 0, "opening the panel records the session on the HOST origin",
        first.slice(0, 90));

  await rp.goto(`${HOST}/second.html`, { waitUntil: "networkidle2" });
  await rp.waitForFunction(() => [...document.body.children].some((el) => el.shadowRoot),
                           { timeout: 15000 });
  const after = await rp.evaluate(() => {
    try { return Object.keys(localStorage).filter((k) => /powabase|widget/i.test(k))
      .map((k) => `${k}=${localStorage.getItem(k)}`).join("|"); } catch { return ""; }
  });
  check(after === first, "navigating to another page on the host keeps the same session",
        after === first ? "unchanged" : `${first} -> ${after}`);
  await rp.close();
}

await browser.close();
server.close();

const failed = results.filter((r) => !r.ok);
const skipped = results.filter((r) => r.skipped);
console.log(`\n${results.length - failed.length - skipped.length}/${results.length - skipped.length} checks passed, ${skipped.length} left for a human`);
if (failed.length) { console.log("FAILED:"); failed.forEach((f) => console.log("  - " + f.label)); process.exit(1); }
