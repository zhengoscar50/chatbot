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

function widgetVars(accent) {
  return `:host{--w-accent:${accent};}`;
}

const WIDGET_CSS = `
:host{
  all: initial;
  --w-bg: #ffffff;
  --w-line: #e3e4e8;
  --w-tab-w: 34px;
  --w-panel-w: 400px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
*{box-sizing:border-box;}

.wrap{
  position: fixed;
  top: 0;
  bottom: 0;
  z-index: 2147483000;   /* below the max, so a host modal can still win */
  display: flex;
  align-items: center;
  pointer-events: none;  /* the gap between tab and panel stays clickable */
}
.wrap[data-side="right"]{ right: 0; flex-direction: row; }
.wrap[data-side="left"]{ left: 0; flex-direction: row-reverse; }

.tab{
  pointer-events: auto;
  flex: none;
  width: var(--w-tab-w);
  padding: 14px 0;
  border: 0;
  border-radius: 8px 0 0 8px;
  background: var(--w-accent);
  color: #fff;
  font-weight: 600;
  font-size: 12px;
  line-height: 1;
  letter-spacing: .08em;
  text-transform: uppercase;
  writing-mode: vertical-rl;
  cursor: pointer;
  box-shadow: 0 2px 14px rgba(0,0,0,.18);
}
.wrap[data-side="left"] .tab{ border-radius: 0 8px 8px 0; transform: rotate(180deg); }
.tab:focus-visible{ outline: 2px solid #fff; outline-offset: -4px; }

.panel{
  pointer-events: auto;
  width: var(--w-panel-w);
  max-width: 100vw;
  height: 100%;
  background: var(--w-bg);
  border-left: 1px solid var(--w-line);
  box-shadow: -8px 0 30px rgba(0,0,0,.18);
  transform: translateX(100%);
  transition: transform 220ms ease;
}
.wrap[data-side="left"] .panel{
  border-left: 0;
  border-right: 1px solid var(--w-line);
  box-shadow: 8px 0 30px rgba(0,0,0,.18);
  transform: translateX(-100%);
}
.wrap[data-open] .panel{ transform: translateX(0); }

.panel iframe{ display:block; width:100%; height:100%; border:0; }

@media (max-width: 640px){
  :host{ --w-panel-w: 100vw; }
  .wrap{ align-items: flex-end; }
  .tab{
    writing-mode: horizontal-tb;
    width: auto;
    padding: 10px 16px;
    margin: 0 0 16px 0;
    border-radius: 999px;
  }
  .wrap[data-side="left"] .tab{ transform: none; border-radius: 999px; }
  .wrap[data-open] .tab{ display: none; }  /* a full-width panel owns the screen */
}

@media (prefers-reduced-motion: reduce){
  .panel{ transition: none; }
}
`;
