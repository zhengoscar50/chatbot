// A deliberately small markdown parser for assistant answers.
//
// It returns TOKENS, never HTML. The renderer builds elements with
// createElement/textContent, so no HTML string is ever produced and untrusted
// text cannot become markup. Answers summarise documents the user did not
// write — a poisoned PDF can put anything into an answer — so the safe
// property is structural rather than a matter of escaping correctly.
//
// Links are deliberately not parsed: `[text](url)` stays literal text. That
// also means citation markers like [1] pass straight through, which the
// reference list depends on.
//
// Emphasis does not nest. "**bold with *italic* inside**" renders as one bold
// run containing asterisks. Models rarely emit it and supporting it would cost
// more than it returns.

const FENCE = /^```/;
const HEADING = /^(#{1,3})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

// Ordered: `**` must be tried before `*`, or bold parses as two italics.
const INLINE = /`([^`]+)`|\*\*([^*]+?)\*\*|__([^_]+?)__|\*([^*\n]+?)\*|_([^_\n]+?)_/;

function parseInline(text) {
  const spans = [];
  let rest = text;
  while (rest) {
    const m = INLINE.exec(rest);
    if (!m) {
      spans.push({ type: "text", text: rest });
      break;
    }
    if (m.index > 0) spans.push({ type: "text", text: rest.slice(0, m.index) });
    if (m[1] !== undefined) spans.push({ type: "code", text: m[1] });
    else if (m[2] !== undefined) spans.push({ type: "strong", text: m[2] });
    else if (m[3] !== undefined) spans.push({ type: "strong", text: m[3] });
    else if (m[4] !== undefined) spans.push({ type: "em", text: m[4] });
    else spans.push({ type: "em", text: m[5] });
    rest = rest.slice(m.index + m[0].length);
  }
  return spans.filter((s) => s.type !== "text" || s.text !== "");
}

function parseMarkdown(input) {
  const text = typeof input === "string" ? input : "";
  const lines = text.split(/\r?\n/);
  const tokens = [];
  let paragraph = [];
  let list = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      tokens.push({ type: "paragraph", spans: parseInline(paragraph.join(" ")) });
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      tokens.push(list);
      list = null;
    }
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    if (FENCE.test(line)) {
      flushAll();
      const body = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      tokens.push({ type: "code", text: body.join("\n") });
      continue;
    }

    if (!line.trim()) {
      flushAll();
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flushAll();
      tokens.push({
        type: "heading",
        level: heading[1].length,
        spans: parseInline(heading[2]),
      });
      continue;
    }

    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      flushParagraph();
      const ordered = Boolean(numbered);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { type: "list", ordered, items: [] };
      }
      list.items.push(parseInline((bullet || numbered)[1]));
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  flushAll();
  return tokens;
}

// Group citations so one document appears once, however many of its passages
// were retrieved. Powabase returns one citation per chunk, so an answer drawing
// on six passages of a PDF used to list that PDF six times.
//
// Every key is preserved: the [n] markers in the answer point at citation keys,
// so dropping the extras would leave markers in the text with nothing to
// resolve to.
function dedupeCitations(citations) {
  const groups = [];
  const byKey = new Map();
  (citations || []).forEach((citation, index) => {
    const isString = typeof citation === "string";
    const name = isString
      ? citation
      : citation.source_name || citation.source_id || "source";
    // source_id is the real identity; two documents can share a filename.
    const identity = isString ? citation : citation.source_id || name;
    const marker = String(isString ? index + 1 : citation.key || index + 1);
    const excerpt = isString ? "" : citation.text_excerpt || "";

    const existing = byKey.get(identity);
    if (existing) {
      if (!existing.markers.includes(marker)) existing.markers.push(marker);
      existing.count += 1;
      return;
    }
    const group = { identity, name, markers: [marker], count: 1, excerpt };
    byKey.set(identity, group);
    groups.push(group);
  });
  return groups;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { parseMarkdown, parseInline, dedupeCitations };
}
