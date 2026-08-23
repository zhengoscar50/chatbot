import { readFileSync } from "fs";

const FE = "/Users/oscar/Downloads/rag-chatbot/frontend";
const results = [];
function check(ok, label, detail = "") {
  results.push(ok);
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
}

const src = readFileSync(`${FE}/tour-steps.js`, "utf8");
const scope = {};
new Function("exports", src +
  "\nexports.TOUR_STEPS = TOUR_STEPS; exports.stepMode = stepMode; exports.stepCopy = stepCopy;")(scope);
const { TOUR_STEPS, stepMode, stepCopy } = scope;

const NOTHING_DONE = { steps: [
  { id: "chatbot", done: true }, { id: "agent", done: false },
  { id: "description", done: false }, { id: "knowledge", done: false },
  { id: "answer", done: false }] };
const ALL_DONE = { steps: NOTHING_DONE.steps.map((s) => ({ ...s, done: true })) };

console.log("\n=== the table itself ===");
{
  check(TOUR_STEPS.length === 8, "eight steps", String(TOUR_STEPS.length));
  check(new Set(TOUR_STEPS.map((s) => s.id)).size === 8, "step ids are unique");
  check(TOUR_STEPS.every((s) => typeof s.target === "string" && s.target),
        "every step names a target selector");
  check(TOUR_STEPS.every((s) => typeof s.needs === "string" || s.needs === null),
        "every step declares which onboarding id it needs, or null");
  check(TOUR_STEPS.every((s) => s.doing.trim() && s.showing.trim() && s.title.trim()),
        "every step carries copy for both modes");
}

console.log("\n=== the description step is the one this tour exists for ===");
{
  const d = TOUR_STEPS.find((s) => s.needs === "description");
  check(!!d, "a step maps to the description requirement");
  check(d.target === "#agent-description", "it targets the real field", d.target);
  // If this copy does not say why the field matters, the tour has not solved
  // the problem it was built for — it has only pointed at a text input.
  check(/rout/i.test(d.doing), "its copy explains that routing matches on it");
}

console.log("\n=== mode selection is what makes replay coherent ===");
{
  const agentStep = TOUR_STEPS.find((s) => s.needs === "agent");

  check(stepMode(agentStep, NOTHING_DONE) === "doing",
        "an unmet step tells the user to act");
  check(stepMode(agentStep, ALL_DONE) === "showing",
        "a step whose work is already done just points at it");

  // The failure this prevents: a fully set-up account presses ? and is ordered
  // to create a second copy of everything it already has.
  const modes = TOUR_STEPS.map((s) => stepMode(s, ALL_DONE));
  check(modes.every((m) => m === "showing"),
        "a complete account gets no instruction to create anything",
        modes.join(","));
}

console.log("\n=== orientation steps never demand work ===");
{
  const orientation = TOUR_STEPS.filter((s) => s.needs === null);
  check(orientation.length >= 1, "there is at least one orientation step");
  check(orientation.every((s) => stepMode(s, NOTHING_DONE) === "showing"),
        "orientation steps show even on an empty account");
}

console.log("\n=== missing or malformed onboarding data degrades to showing ===");
{
  // The tour must still run if /onboarding failed. Showing every step is the
  // safe degradation: it teaches, and it never orders an action whose
  // completion the engine cannot detect.
  const s = TOUR_STEPS.find((x) => x.needs === "agent");
  check(stepMode(s, null) === "showing", "null onboarding -> showing");
  check(stepMode(s, {}) === "showing", "no steps key -> showing");
  check(stepMode(s, { steps: [] }) === "showing", "empty steps -> showing");
}

console.log("\n=== copy selection ===");
{
  const s = TOUR_STEPS.find((x) => x.needs === "agent");
  check(stepCopy(s, "doing").body === s.doing, "doing mode uses the doing copy");
  check(stepCopy(s, "showing").body === s.showing, "showing mode uses the showing copy");
  check(stepCopy(s, "doing").title === s.title, "title is shared by both modes");
}

const failed = results.filter((r) => !r).length;
console.log(`\n${results.length} checks, ${results.length - failed} passed, ${failed} FAILED`);
process.exit(failed ? 1 : 0);
