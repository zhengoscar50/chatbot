// The widget's styles, as a string injected into its shadow root.
//
// Kept apart from the loader so the styles can be read as styles. Everything
// here is scoped by the shadow boundary, so class names are short and cannot
// collide with the host page — and nothing here escapes to touch their layout.
//
// Nothing inherits. A host page may set any font, colour or box-sizing on body,
// and inherited values cross a shadow boundary. The tab explicitly states every
// property it depends on; the panel deliberately states almost nothing because
// it only holds an iframe.

(function () {
function widgetVars(accent) {
  return `:host{--w-accent:${accent};}`;
}

const WIDGET_CSS = `
:host{
  all: initial;
  --w-bg: #ffffff;
  --w-line: #e3e4e8;
  --w-bubble: 52px;
  --w-card-w: 360px;
  --w-card-h: 520px;
  --w-edge: 20px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
*{box-sizing:border-box;}

/* Anchored to a corner, stacked bottom-up: the card sits above the bubble it
   opens from, which is what makes the two read as one object rather than a
   panel that happened to appear. */
.wrap{
  position: fixed;
  bottom: var(--w-edge);
  z-index: 2147483000;   /* below the max, so a host modal can still win */
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 12px;
  pointer-events: none;  /* only the bubble and card take clicks */
}
.wrap[data-side="right"]{ right: var(--w-edge); }
.wrap[data-side="left"]{ left: var(--w-edge); align-items: flex-start; }

.tab{
  pointer-events: auto;
  order: 2;              /* always beneath the card, whichever way they stack */
  flex: none;
  width: var(--w-bubble);
  height: var(--w-bubble);
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: var(--w-accent);
  color: #fff;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 16px rgba(0,0,0,.24);
  transition: transform 160ms ease;
}
.tab:hover{ transform: scale(1.06); }
.tab:focus-visible{ outline: 2px solid #fff; outline-offset: -5px; }
.tab svg{ width: 24px; height: 24px; display: block; }

/* The glyph swaps rather than the button disappearing. The bubble stays put
   and becomes the close control, so there is one thing to press either way. */
.tab .ico-close{ display: none; }
.wrap[data-open] .tab .ico-chat{ display: none; }
.wrap[data-open] .tab .ico-close{ display: block; }

.panel{
  pointer-events: auto;
  order: 1;
  width: var(--w-card-w);
  max-width: calc(100vw - (var(--w-edge) * 2));
  height: var(--w-card-h);
  /* Never taller than the room left above the bubble. */
  max-height: calc(100vh - var(--w-bubble) - (var(--w-edge) * 2) - 24px);
  background: var(--w-bg);
  border: 1px solid var(--w-line);
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 12px 40px rgba(0,0,0,.22);
  transform-origin: bottom right;
  transform: scale(.94) translateY(8px);
  opacity: 0;
  visibility: hidden;
  transition: transform 160ms ease, opacity 160ms ease, visibility 0s linear 160ms;
}
.wrap[data-side="left"] .panel{ transform-origin: bottom left; }

.wrap[data-open] .panel{
  transform: scale(1) translateY(0);
  opacity: 1;
  visibility: visible;
  transition: transform 160ms ease, opacity 160ms ease, visibility 0s;
}

.panel iframe{ display:block; width:100%; height:100%; border:0; }

@media (max-width: 640px){
  :host{ --w-edge: 12px; --w-card-h: 70vh; }
  .panel{
    /* Near-full-width, deliberately not edge to edge: a card that fills the
       screen stops reading as a widget on someone's page and starts reading
       as an app that took it over. */
    width: calc(100vw - (var(--w-edge) * 2));
  }
}

@media (prefers-reduced-motion: reduce){
  .tab, .panel{ transition: none; }
}
`;

  // One namespaced object, not two bare globals. This file is injected into
  // other people's pages by widget.js, whose whole premise is that it adds a
  // single name to their window — a premise two loose top-level declarations
  // quietly broke.
  window.__powabaseWidgetCSS = { css: WIDGET_CSS, vars: widgetVars };
})();
