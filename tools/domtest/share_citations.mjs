// The share page's citation excerpt normaliser.
//
// Pure arithmetic on a string, so it needs no DOM at all — which is the point.
// The visible bug it fixes (a wall of markdown burying the answer in a 360px
// widget card) is a layout problem no harness here can see, but the text
// transform underneath it can be pinned exactly.
import { readFileSync } from "fs";
import { JSDOM } from "jsdom";

// Overridden by run-all.sh so the harness is not tied to one machine.
const FE = process.env.RAGCHAT_FE || "/Users/oscar/Downloads/rag-chatbot/frontend";
const results = [];
function check(ok, label, detail = "") {
  results.push(ok);
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
}

// share.js is a browser script with top-level DOM lookups, so it cannot simply
// be evaluated here. Lift the one function under test out of the source.
const src = readFileSync(`${FE}/share.js`, "utf8");
const start = src.indexOf("function excerptText(");
const end = src.indexOf("\n}", start) + 2;
if (start === -1) {
  console.log("  [FAIL] excerptText not found in share.js");
  process.exit(1);
}
const excerptText = new Function(`${src.slice(start, end)}; return excerptText;`)();

console.log("\n=== share citations: excerpt normalisation ===");

// Taken verbatim from a real stored citation. 47 of 56 in the live project
// open on a heading exactly like this.
const REAL = "# EMPLOYEE HANDBOOK – LEAVE AND BENEFITS\n\n## Leave\nNew employees "
  + "accrue leave at TWO POINT FIVE DAYS per month.  \nCarry-over is capped at TEN DAYS";

{
  const out = excerptText(REAL);
  check(!out.includes("#"),
        "heading markers are gone", out.slice(0, 34));
  check(!out.includes("\n"),
        "newlines are flattened to one line");
  check(!/\s{2,}/.test(out),
        "no runs of whitespace survive", JSON.stringify(out.slice(30, 60)));
  check(out.startsWith("EMPLOYEE HANDBOOK"),
        "the prose itself is untouched", out.slice(0, 26));
  check(out.includes("TWO POINT FIVE DAYS"),
        "content in the middle is not dropped");
}

{
  check(excerptText("- first\n- second") === "first second",
        "list bullets are stripped", excerptText("- first\n- second"));
  check(excerptText("**bold** and `code`") === "bold and code",
        "emphasis and code ticks are stripped", excerptText("**bold** and `code`"));
}

{
  // The redactor emits "" rather than null when a source has no excerpt, and a
  // replayed transcript can carry either. Neither may throw: a citation that
  // cannot render is a missing line, but an exception loses the whole turn.
  check(excerptText("") === "", "empty string survives");
  check(excerptText(null) === "", "null survives");
  check(excerptText(undefined) === "", "undefined survives");
  check(excerptText(42) === "42", "a non-string is coerced, not thrown at");
}

{
  // Nothing here should shorten the text — the clamp is CSS, so a change of
  // mind about how much to show never means re-running a transform over data.
  const long = "word ".repeat(80);
  check(excerptText(long).length > 300,
        "truncation is left to CSS, not baked into the text",
        `${excerptText(long).length} chars`);
}

console.log("\n=== share citations: one row per document ===");
{
  // Powabase returns a citation per retrieved PASSAGE. Taken from the live
  // project: one real turn carried seven, every one of them from the same PDF
  // and every one labelled "Source 1" after redaction. Rendered ungrouped that
  // reads as seven sources and is really one.
  const dom = new JSDOM(readFileSync(`${FE}/share.html`, "utf8"),
                        { runScripts: "outside-only", url: "http://localhost/s/tok" });
  const w = dom.window, d = w.document;
  w.eval(readFileSync(`${FE}/markdown.js`, "utf8"));
  const src = readFileSync(`${FE}/share.js`, "utf8");
  const cut = (name) => {
    const i = src.indexOf(`function ${name}(`);
    return src.slice(i, src.indexOf("\n}\n", i) + 2);
  };
  w.eval(`${cut("excerptText")}\n${cut("citations")}\nvar thread=document.getElementById("thread");`);

  const seven = Array.from({ length: 7 }, (_, i) => ({
    key: i + 1, source_name: "Source 1", text_excerpt: `passage ${i + 1}`,
  }));
  w.eval(`citations(${JSON.stringify(seven)})`);
  const rows = [...d.querySelectorAll(".citation")];

  check(rows.length === 1,
        "seven passages of one document render as one row", `${rows.length} rows`);
  check(/\[1\]\[2\]\[3\]\[4\]\[5\]\[6\]\[7\]/.test(rows[0].textContent),
        "every marker is still findable from the prose",
        rows[0].textContent.slice(0, 30));
  check(/7 passages/.test(rows[0].textContent),
        "the row says how many passages it stands for");

  // Two genuinely different documents must stay two rows — otherwise the fix
  // would hide real breadth instead of collapsing false breadth.
  const two = [
    { key: 1, source_name: "Source 1", text_excerpt: "a" },
    { key: 2, source_name: "Source 2", text_excerpt: "b" },
  ];
  w.eval(`thread.innerHTML = ""; citations(${JSON.stringify(two)})`);
  check(d.querySelectorAll(".citation").length === 2,
        "two different documents still render as two rows",
        `${d.querySelectorAll(".citation").length} rows`);

  // A single citation gets no count suffix: "· 1 passages" is both wrong and
  // noise, and 32 of the 38 live turns carry exactly one.
  w.eval(`thread.innerHTML = ""; citations(${JSON.stringify([two[0]])})`);
  check(!/passages/.test(d.querySelector(".citation").textContent),
        "a lone citation carries no passage count",
        d.querySelector(".citation").textContent.slice(0, 30));
}

const failed = results.filter((r) => !r).length;
console.log(`\n${results.length} checks, ${results.length - failed} passed, ${failed} FAILED`);
process.exit(failed ? 1 : 0);
