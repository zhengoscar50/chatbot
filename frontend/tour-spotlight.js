// Where the tour's four dimming panes and its explanation box go.
//
// Four panes rather than one overlay with a transparent hole, because an
// overlay covers the target: clicks land on the overlay instead of the button
// the tour just asked you to press. Setting pointer-events:none fixes that and
// creates a worse problem — every click passes through, so the user wanders
// off-script mid-tour. Four panes make the geometry do the work: the target is
// genuinely uncovered and is the only clickable thing on the page.
//
// Pure functions over plain objects on purpose. jsdom has no layout engine, so
// arithmetic kept here is the only part of the spotlight that can be tested.

// Clamp so an edge-hugging target cannot produce a negative pane. A negative
// width renders as a full-viewport pane in some browsers, which would dim the
// very thing being highlighted.
function positive(n) {
  return n > 0 ? n : 0;
}

function panesFor(rect, viewport, pad) {
  const p = pad || 0;
  const top = positive(rect.y - p);
  const left = positive(rect.x - p);
  const right = rect.x + rect.width + p;
  const bottom = rect.y + rect.height + p;

  return {
    top: { x: 0, y: 0, width: viewport.width, height: top },
    bottom: { x: 0, y: bottom, width: viewport.width, height: positive(viewport.height - bottom) },
    left: { x: 0, y: top, width: left, height: positive(bottom - top) },
    right: { x: right, y: top, width: positive(viewport.width - right), height: positive(bottom - top) },
  };
}

function boxPlacement(rect, viewport, box, gap) {
  const g = gap || 0;

  // Nowhere beside anything fits on a phone. Dock rather than overflow.
  if (viewport.width < box.width + rect.width + g * 3) {
    return {
      x: Math.max(0, Math.round((viewport.width - box.width) / 2)),
      y: positive(viewport.height - box.height - g),
      side: "docked",
    };
  }

  const clampY = (y) => Math.min(positive(viewport.height - box.height), positive(y));
  const centredY = clampY(rect.y + rect.height / 2 - box.height / 2);

  const rightX = rect.x + rect.width + g;
  if (rightX + box.width <= viewport.width) {
    return { x: rightX, y: centredY, side: "right" };
  }

  const leftX = rect.x - g - box.width;
  if (leftX >= 0) {
    return { x: leftX, y: centredY, side: "left" };
  }

  const clampX = (x) => Math.min(positive(viewport.width - box.width), positive(x));
  const centredX = clampX(rect.x + rect.width / 2 - box.width / 2);

  const belowY = rect.y + rect.height + g;
  if (belowY + box.height <= viewport.height) {
    return { x: centredX, y: belowY, side: "below" };
  }
  return { x: centredX, y: clampY(rect.y - g - box.height), side: "above" };
}
