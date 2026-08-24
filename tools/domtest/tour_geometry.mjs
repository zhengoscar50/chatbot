// Pure geometry for the tour's spotlight. No DOM, no layout engine needed —
// which is the point: jsdom cannot lay anything out, so the arithmetic lives
// here where it can actually be tested.
import { readFileSync } from "fs";

const FE = "/Users/oscar/Downloads/rag-chatbot/frontend";
const results = [];
function check(ok, label, detail = "") {
  results.push(ok);
  console.log(`  [${ok ? "ok  " : "FAIL"}] ${label}${detail ? "  — " + detail : ""}`);
}

// Load the module into this scope the same way the browser would: no module
// system exists in this project, so eval the file and pull the globals out.
const src = readFileSync(`${FE}/tour-spotlight.js`, "utf8");
const scope = {};
new Function("exports", src + "\nexports.panesFor = panesFor; exports.boxPlacement = boxPlacement;")(scope);
const { panesFor, boxPlacement } = scope;

const VIEW = { width: 1000, height: 800 };

console.log("\n=== panes cover everything except the target ===");
{
  const rect = { x: 400, y: 300, width: 200, height: 100 };
  const p = panesFor(rect, VIEW, 0);

  check(p.top.y === 0 && p.top.height === 300, "top pane runs from the top edge to the target");
  check(p.bottom.y === 400 && p.bottom.height === 400, "bottom pane runs from the target to the bottom edge");
  check(p.left.x === 0 && p.left.width === 400, "left pane runs from the left edge to the target");
  check(p.right.x === 600 && p.right.width === 400, "right pane runs from the target to the right edge");

  // The whole point: the target rect is uncovered, so it is the only thing
  // on the page that can be clicked. If a pane overlapped it, the tour would
  // silently swallow the click it is asking the user to make.
  const overlaps = (a, b) =>
    a.x < b.x + b.width && a.x + a.width > b.x &&
    a.y < b.y + b.height && a.y + a.height > b.y;
  const offenders = Object.entries(p).filter(([, pane]) => overlaps(pane, rect));
  check(offenders.length === 0, "no pane overlaps the target rect",
        offenders.map(([n]) => n).join(","));
}

console.log("\n=== padding grows the hole, never the panes ===");
{
  const rect = { x: 400, y: 300, width: 200, height: 100 };
  const p = panesFor(rect, VIEW, 8);
  check(p.top.height === 292, "padding pulls the top pane back", String(p.top.height));
  check(p.left.width === 392, "padding pulls the left pane back", String(p.left.width));
}

console.log("\n=== a target at the very edge produces no negative panes ===");
{
  // A card flush against the left edge, or a button at the top of the page.
  // A negative width renders as a full-viewport pane in some browsers, which
  // would dim the target itself.
  const p = panesFor({ x: 0, y: 0, width: 100, height: 50 }, VIEW, 12);
  const negatives = Object.entries(p).filter(([, q]) => q.width < 0 || q.height < 0);
  check(negatives.length === 0, "no pane has a negative dimension",
        negatives.map(([n]) => n).join(","));
}

console.log("\n=== the box goes where there is room ===");
{
  const box = { width: 320, height: 160 };

  // Room on the right.
  let b = boxPlacement({ x: 100, y: 300, width: 120, height: 40 }, VIEW, box, 16);
  check(b.side === "right", "prefers the right of a left-hand target", b.side);

  // No room on the right — must flip rather than run off the viewport.
  b = boxPlacement({ x: 850, y: 300, width: 120, height: 40 }, VIEW, box, 16);
  check(b.side === "left", "flips to the left when the right would overflow", b.side);
  check(b.x >= 0, "flipped box stays on screen", String(b.x));

  // Neither side fits.
  b = boxPlacement({ x: 300, y: 40, width: 400, height: 40 }, VIEW, box, 16);
  check(b.side === "below", "drops below when neither side fits", b.side);
}

console.log("\n=== a narrow viewport docks the box ===");
{
  // On a phone there is no room beside anything. Docking is the documented
  // behaviour; overflowing is not.
  const b = boxPlacement({ x: 20, y: 300, width: 280, height: 40 },
                         { width: 360, height: 640 }, { width: 320, height: 160 }, 16);
  check(b.side === "docked", "docks on a narrow viewport", b.side);
}

console.log("\n=== the box never leaves the viewport, wherever the target is ===");
{
  // Property check rather than another example: sweep the target across the
  // whole viewport and assert the invariant holds every time.
  const box = { width: 320, height: 160 };
  let escaped = null;
  for (let x = 0; x <= 960 && !escaped; x += 40) {
    for (let y = 0; y <= 760 && !escaped; y += 40) {
      const b = boxPlacement({ x, y, width: 40, height: 40 }, VIEW, box, 16);
      if (b.x < 0 || b.y < 0 || b.x + box.width > VIEW.width || b.y + box.height > VIEW.height) {
        escaped = `target ${x},${y} -> box ${b.x},${b.y} (${b.side})`;
      }
    }
  }
  check(!escaped, "box stays on screen for every target position", escaped || "");
}

console.log("\n=== a box larger than the viewport pins to the origin ===");
{
  const big = { width: 320, height: 160 };

  // Wide enough to skip docking, short enough that the box exceeds the height.
  // This is the case that actually reaches the clamps — a viewport narrow
  // enough to dock returns before them and proves nothing about clamping.
  const shortWide = { width: 1000, height: 100 };
  const b = boxPlacement({ x: 10, y: 10, width: 40, height: 40 }, shortWide, big, 16);
  check(b.side === "right", "a short wide viewport takes a side branch, not docking", b.side);
  check(b.y >= 0, "the vertical clamp never goes negative when the box is taller than the viewport",
        String(b.y));

  // And a viewport too small in both directions still degrades from 0,0
  // rather than to a negative offset.
  const d = boxPlacement({ x: 10, y: 10, width: 40, height: 40 },
                         { width: 300, height: 100 }, big, 16);
  check(d.x >= 0 && d.y >= 0, "docked mode also never goes negative", `${d.x},${d.y}`);

  // The ordinary invariant still holds whenever the box does fit.
  const fits = boxPlacement({ x: 10, y: 10, width: 40, height: 40 },
                            { width: 1000, height: 800 }, big, 16);
  check(fits.x + big.width <= 1000 && fits.y + big.height <= 800,
        "stays fully on screen whenever the box fits");
}

console.log("\n=== the box keeps clear of every edge, not merely inside it ===");
{
  // Clamping to the exact edge left the box flush against the bottom of the
  // window, where a pixel of rounding or a line of text reflowing taller than
  // the measurement shaves off the last row — reported as the box being "cut
  // off a bit" on the explaining steps. Inside-the-viewport is not the
  // invariant; a margin is.
  const box = { width: 320, height: 160 };
  const gap = 16;
  const views = [{ width: 1000, height: 800 }, { width: 1440, height: 700 }];
  let tight = null;

  for (const view of views) {
    for (let x = 0; x <= view.width && !tight; x += 37) {
      for (let y = 0; y <= view.height && !tight; y += 37) {
        const b = boxPlacement({ x, y, width: 40, height: 40 }, view, box, gap);
        if (b.side === "docked") continue;   // docking owns its own placement
        const clear = b.x >= gap && b.y >= gap
          && b.x + box.width <= view.width - gap
          && b.y + box.height <= view.height - gap;
        if (!clear) {
          tight = `${view.width}x${view.height} target ${x},${y} -> ${b.x},${b.y} (${b.side})`;
        }
      }
    }
  }
  check(!tight, "the box never sits flush against an edge", tight || "");
}

const failed = results.filter((r) => !r).length;
console.log(`\n${results.length} checks, ${results.length - failed} passed, ${failed} FAILED`);
process.exit(failed ? 1 : 0);
