# Frontend DOM harness

Loads the real `index.html`, `styles.css` and all eight scripts into jsdom,
stubs the backend, and drives the UI. Written after the dashboard shipped with
three bugs that every static check passed and a person spotted immediately.

**Not part of any test suite, and deliberately not wired into one.** The repo has
no dependency manifest and keeps none, so jsdom is installed *outside* it:

```bash
mkdir -p /tmp/domtest && cd /tmp/domtest
npm init -y && npm install jsdom
cp <repo>/tools/domtest/*.mjs .
node run.mjs         # 48 checks: boot, cards, navigation, resume, menus, CRUD, failure states
node scope.mjs       # 14 checks: per-chat agent exclusion, end to end
node onboarding.mjs  # 24 checks: the getting-started panel, its Help toggle, both hint modes, and malformed /onboarding bodies
```

Both exit non-zero on failure and print a per-check line.

## What it can and cannot see

It executes real behaviour: screen switching, card rendering, keyboard access,
XSS, `sessionStorage` resume, rename/create/delete, the last-chatbot refusal,
retry recovery, and the request budget.

**It has no layout engine.** It cannot tell you whether the grid is spaced
sensibly, whether a menu opens off the edge of its card, or whether dark mode
reads well. Every user-visible bug in this feature so far has been one of those.

## One trap worth knowing

`getComputedStyle` **cannot** verify the `[hidden]` cascade. jsdom special-cases
the `hidden` attribute instead of running the cascade, so it reports
`display: none` whether or not an author rule like `.app { display: flex }` would
really win in a browser. An assertion written that way passes with the guard rule
deleted — it is a test that cannot fail.

Section L of `run.mjs` does it correctly instead: it walks every element the app
actually hides at runtime, then reads `styles.css` statically to check each
selector either does not set `display` or has a matching `[hidden]` guard. That
version was mutation-tested — delete `.bot-card__actions[hidden]` from
`styles.css` and it fails, naming the selector.
