// node --test frontend/
const test = require("node:test");
const assert = require("node:assert");
const { parseMarkdown, parseInline, dedupeCitations } = require("./markdown.js");

// --- inline ----------------------------------------------------------------

test("bold is parsed before italic", () => {
  assert.deepStrictEqual(parseInline("**Corridor Seven**"), [
    { type: "strong", text: "Corridor Seven" },
  ]);
});

test("italic, inline code and surrounding text", () => {
  assert.deepStrictEqual(parseInline("use `top_k` for *tuning*"), [
    { type: "text", text: "use " },
    { type: "code", text: "top_k" },
    { type: "text", text: " for " },
    { type: "em", text: "tuning" },
  ]);
});

test("citation markers survive untouched", () => {
  // [1] resembles link syntax. Links are not parsed at all, which is what
  // keeps the reference list working.
  assert.deepStrictEqual(parseInline("in Corridor Seven [1]."), [
    { type: "text", text: "in Corridor Seven [1]." },
  ]);
});

test("a link is left as literal text", () => {
  const spans = parseInline("see [docs](javascript:alert(1))");
  assert.strictEqual(spans.length, 1);
  assert.strictEqual(spans[0].type, "text");
  assert.match(spans[0].text, /javascript:alert/);
});

// --- blocks ----------------------------------------------------------------

test("bullet list", () => {
  const tokens = parseMarkdown("- Hold your eyelids open\n- Remove contacts");
  assert.strictEqual(tokens.length, 1);
  assert.strictEqual(tokens[0].type, "list");
  assert.strictEqual(tokens[0].ordered, false);
  assert.strictEqual(tokens[0].items.length, 2);
});

test("numbered list is separate from a bullet list", () => {
  const tokens = parseMarkdown("- a\n1. b");
  assert.deepStrictEqual(tokens.map((t) => t.ordered), [false, true]);
});

test("headings", () => {
  const [h] = parseMarkdown("### Spill response");
  assert.strictEqual(h.type, "heading");
  assert.strictEqual(h.level, 3);
});

test("fenced code keeps its content verbatim", () => {
  const [code] = parseMarkdown("```\nline **not bold**\n```");
  assert.strictEqual(code.type, "code");
  assert.strictEqual(code.text, "line **not bold**");
});

test("blank lines separate paragraphs", () => {
  const tokens = parseMarkdown("first para\nstill first\n\nsecond para");
  assert.strictEqual(tokens.length, 2);
  assert.deepStrictEqual(tokens[0].spans, [{ type: "text", text: "first para still first" }]);
});

test("markup in the input never becomes a tag", () => {
  // The parser only ever emits text; the renderer uses textContent. This
  // asserts the parser does not smuggle raw html into a token.
  const tokens = parseMarkdown('<img src=x onerror="alert(1)"> and <script>bad()</script>');
  const text = JSON.stringify(tokens);
  assert.match(text, /img src=x/);          // preserved as literal text
  assert.strictEqual(tokens[0].type, "paragraph");
  assert.ok(tokens[0].spans.every((s) => typeof s.text === "string"));
});

test("empty and non-string input are tolerated", () => {
  assert.deepStrictEqual(parseMarkdown(""), []);
  assert.deepStrictEqual(parseMarkdown(null), []);
  assert.deepStrictEqual(parseMarkdown(undefined), []);
});

// --- citations -------------------------------------------------------------

test("six passages of one document collapse to one row", () => {
  const citations = Array.from({ length: 6 }, (_, i) => ({
    key: String(i + 1), source_id: "src-1", source_name: "00002480A_Ramble.pdf",
    text_excerpt: "excerpt " + i,
  }));

  const groups = dedupeCitations(citations);

  assert.strictEqual(groups.length, 1);
  assert.strictEqual(groups[0].count, 6);
  // Every marker is kept: [4] in the answer must still resolve to a row.
  assert.deepStrictEqual(groups[0].markers, ["1", "2", "3", "4", "5", "6"]);
  assert.strictEqual(groups[0].excerpt, "excerpt 0");
});

test("distinct documents stay separate, in first-appearance order", () => {
  const groups = dedupeCitations([
    { key: "1", source_id: "b", source_name: "second.pdf" },
    { key: "2", source_id: "a", source_name: "first.pdf" },
    { key: "3", source_id: "b", source_name: "second.pdf" },
  ]);

  assert.deepStrictEqual(groups.map((g) => g.name), ["second.pdf", "first.pdf"]);
  assert.deepStrictEqual(groups[0].markers, ["1", "3"]);
});

test("two documents sharing a filename are not merged", () => {
  const groups = dedupeCitations([
    { key: "1", source_id: "a", source_name: "notes.pdf" },
    { key: "2", source_id: "b", source_name: "notes.pdf" },
  ]);
  assert.strictEqual(groups.length, 2);
});

test("legacy string citations still work", () => {
  const groups = dedupeCitations(["handbook.pdf", "handbook.pdf", "other.pdf"]);
  assert.strictEqual(groups.length, 2);
  assert.strictEqual(groups[0].count, 2);
});

test("no citations yields no groups", () => {
  assert.deepStrictEqual(dedupeCitations([]), []);
  assert.deepStrictEqual(dedupeCitations(null), []);
});
