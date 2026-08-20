# A dashboard of chatbots

## The change

Today every chatbot is reachable only through a `<select>` in the sidebar. You
can switch chatbots but you cannot **see** them: the picker shows one name at a
time and tells you nothing about what each chatbot contains.

This adds a dashboard — a grid of cards, one per chatbot, each listing that
chatbot's agents. Clicking a card enters the chatbot; a `← Dashboard` button
returns. Logging in lands you on the grid.

Nothing about the data model changes. This is a frontend change with no new
endpoint, no migration, and no backend edit.

## Why a grid rather than a better picker

The picker's problem is not that it is ugly. It is that a chatbot's *contents*
are its identity — a chatbot is "the one with Chem Tutor and Vision in it" —
and a dropdown can never show that. Once you have several chatbots holding
different specialists, a list of names is not enough to choose between them.

The grid also gives chatbot-level actions a home. Create, rename and delete are
currently three sidebar buttons competing with chat controls for the same
space; on a card they belong to the thing they act on.

## Three screens, one page

`app.js` already switches between the login gate and the application with
`element.hidden`. The dashboard is a third section switched exactly the same
way. No router, no build step, no new dependency.

```
#auth-gate        #dashboard              .app
login form   →    the grid of cards  →    sidebar + conversation
                       ↑                       │
                       └───── ← Dashboard ─────┘
```

## What moves

**Out of the sidebar:** the `#chatbot-select` picker, `+ New chatbot`, and
`Delete chatbot`. The grid does all three jobs better.

**Into the topbar:** a `← Dashboard` button, placed left of the title.

**Unchanged in the sidebar:** `+ New chat`, the chat list, `⚙ Manage agents`,
`📚 Chatbot knowledge`, the user label, logout, and the admin link.

The result is a sidebar that is purely about the current chatbot's chats, which
is what it was already trying to be.

## The card

Each card carries:

- the chatbot's **name**, and its description when it has one;
- **up to three agent names**, then `+N more` when there are others;
- the **chat count** (`18 chats`);
- a **`⋯` menu** holding **Rename** and **Delete**.

Clicking anywhere on the card except the `⋯` menu enters that chatbot.

A chatbot with no agents shows **"No agents yet"** rather than an empty list —
a blank region reads as a loading failure.

A final **`+ New chatbot`** tile sits after the last card, so creating one is
part of the same grid rather than a separate control.

The grid is a CSS `grid` with `repeat(auto-fill, minmax(260px, 1fr))`, so it
reflows to one column on a phone without a media query.

## Navigation and persistence

Two requirements could contradict each other — "the dashboard is where you
start" and "a refresh should not throw away where you were" — so the rule is
explicit:

- **`sessionStorage`** holds *"I am currently inside chatbot X."* On load, if
  it names a chatbot that still exists, the app opens that chatbot directly.
  A refresh therefore keeps your place.
- **No `sessionStorage` entry** — a new tab, or the first visit after closing
  the browser — opens the dashboard.
- **The existing `localStorage` key is removed.** It exists today only to
  preselect the picker, and the picker is going. `sessionStorage` now covers
  resuming, and a stored value nothing reads is a vestige that outlives its
  reason — the same shape as the verifier check that asserted one chatbot per
  user long after that stopped being true.
- **Logging out clears the `sessionStorage` entry.**

Entering a chatbot writes `sessionStorage`; pressing `← Dashboard` clears it,
so the next refresh stays on the dashboard rather than bouncing back in.

## Data

No new endpoint. Drawing the grid is:

```
GET /chatbots
  then, per chatbot, in parallel:
    GET /agents?chatbot_id=…
    GET /sessions?chatbot_id=…
```

Five requests for two chatbots, all served from our own database. Requests for
a chatbot fan out with `Promise.all`, so one slow chatbot does not serialise
the rest.

**Document counts stay off the card.** They are the one number that costs a
Powabase round trip per knowledge tier per chatbot, and they are the least
useful thing on a launcher — the knowledge panel shows them the moment you open
it.

## Deleting a chatbot

The existing confirmation keeps its counts, including documents:

> Delete **Work**? This removes 4 agents, 12 chats, and 7 documents.
> This can't be undone.

The document count is fetched **at delete time only** — one call, at the one
moment the number matters. That is why keeping it off the card costs nothing.

On success the grid re-renders. The server still refuses to delete a user's
last chatbot with `400` and a message; the dashboard shows that message
verbatim rather than inventing its own.

## Files

**New:** `frontend/dashboard.js` — renders the grid, handles entering a
chatbot, the `⋯` menu, and the New chatbot tile.

**Changed:** `frontend/chatbots.js` keeps owning chatbot *state* (the list,
`currentChatbotId`, loading) and loses the picker's DOM wiring.
`frontend/app.js` gains the screen switch and the `← Dashboard` button.
`frontend/index.html` gains the dashboard section and loses three sidebar
controls. `frontend/styles.css` gains the grid and card rules.

This split mirrors the one already in the codebase between `agents.js` (state
and forms) and `scope.js` (one focused control).

## Edge cases

- **A chatbot deleted in another tab.** Entering a card whose chatbot has since
  disappeared gets `404`; the dashboard reloads and shows a brief message
  rather than a dead screen.
- **A remembered chatbot that no longer exists.** `sessionStorage` names an id
  that is gone: fall back to the dashboard instead of erroring.
- **Long names.** Truncated with CSS ellipsis, full text in the `title`
  attribute.
- **Hostile names.** Every card field is written with `textContent`, never
  `innerHTML`, exactly as the rest of this codebase does. A chatbot named
  `<script>alert(1)</script>` renders as that literal text.
- **One chatbot.** The grid shows a single card beside the New tile. No special
  case.

## Testing

These modules have no automated coverage and this change does not add a
harness — only `markdown.js` is unit-tested, and building a DOM harness is a
larger piece of work than this feature. That is a real gap, stated plainly
rather than papered over.

What must hold:

- `node --test frontend/*.test.js` stays at 16 passing.
- `python -m pytest -q` stays at 455 passing — this change touches no backend
  file, so any movement means something unintended was edited.

Manual click-through, which is the actual verification:

1. Log in → the dashboard appears with one card per chatbot.
2. Each card lists its own agents; `+8 more` appears when there are over three.
3. Click a card → that chatbot opens with its chats in the sidebar.
4. Refresh → you stay in that chatbot, same chat open.
5. `← Dashboard` → back to the grid; refresh now stays on the grid.
6. Create a chatbot from the tile → a new empty card, "No agents yet".
7. Rename from `⋯` → the card updates.
8. Delete from `⋯` → confirmation names agents, chats and documents.
9. Delete the only chatbot → the server's message is shown, nothing is deleted.
10. Ask a question inside a chatbot → routing is unchanged.

## Risks

**The dashboard becomes load-bearing.** Removing the picker means it is the
only way to switch chatbots. If its fetch fails the user is stranded, so a
failed load renders an error with a retry button, and `← Dashboard` works
regardless of application state.

**Screen switching is new to this app.** Three sections toggled by `hidden` is
simple, but a missed toggle leaves two screens visible at once. Every
transition goes through one pair of functions — `showDashboard()` and
`enterChatbot(id)` — rather than being set inline at call sites, so there is
one place for that bug to live and one place to fix it.

**Boot ordering.** `currentChatbotId` must be set before `loadAgents()` and
`loadSessions()` run. Entering from a card must preserve the order the existing
boot sequence already relies on.

## Out of scope

- **Document counts on cards.** Deliberate, per the data section.
- **Agent management from the dashboard.** Agent names on a card are
  information, not controls; `⚙ Manage agents` inside the chatbot stays the one
  place agents are edited.
- **Moving agents between chatbots.**
- **Sharing or embedding a chatbot.** The single biggest gap against GPT
  Trainer, and its own project.
- **An automated frontend test harness.**
