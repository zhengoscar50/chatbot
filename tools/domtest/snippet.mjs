// The embed snippet is HTML the owner pastes onto somebody else's site. These
// checks run it as HTML rather than inspecting it as a string: a snippet that
// contains the right characters but does not parse is exactly the bug the
// string assertions in the backend suite cannot see.

import { JSDOM, VirtualConsole } from "jsdom";

const results = [];
const check = (ok, label, detail = "") => {
  results.push({ ok, label });
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
};
const flush = async (n = 20) => { for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0)); };

// Exactly what the backend emits, for a host that no longer resolves.
const SNIPPET = `<script src="https://dead-host.example/widget.js" data-token="tok123" async onerror="console.error('Chat widget: could not load from https://dead-host.example . If that address has changed, re-copy the embed snippet from your dashboard.')"></script>`;

console.log("\n=== the snippet parses and its onerror fires ===");
{
  const vc = new VirtualConsole();
  const logged = [];
  vc.on("jsdomError", () => {});          // the failed load itself
  vc.on("error", (...a) => logged.push(a.join(" ")));

  const dom = new JSDOM(`<body>${SNIPPET}</body>`, {
    runScripts: "dangerously", resources: undefined, virtualConsole: vc, url: "https://host-site.example/",
  });
  const d = dom.window.document;

  const tag = d.querySelector("script[data-token]");
  check(tag !== null, "the snippet parses into a single script element");
  check(tag.getAttribute("src") === "https://dead-host.example/widget.js", "with the right src");
  check(tag.getAttribute("data-token") === "tok123", "and the token intact");
  check(tag.hasAttribute("async"), "and async preserved");
  check(tag.hasAttribute("onerror"), "and an onerror handler attached");

  // jsdom does not fetch, so drive the same event the browser would.
  tag.dispatchEvent(new dom.window.Event("error"));
  await flush();

  check(logged.length === 1, "a failed load logs exactly once", `got ${logged.length}`);
  check(/could not load from https:\/\/dead-host\.example/.test(logged[0] || ""),
        "and the message names the address that failed");
  check(/re-copy the embed snippet/.test(logged[0] || ""),
        "and says what to do about it");

  // The site owner's visitors must not be shown our problem.
  check(d.body.textContent.trim() === "", "nothing is rendered onto the host page");
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (failed.length) { console.log("FAILED:"); failed.forEach((f) => console.log("  - " + f.label)); process.exit(1); }
