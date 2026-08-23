# A getting-started checklist

## The problem

A new account gets exactly one thing: an empty chatbot named "My chatbot". No
agents, no documents, no chats.

So the first experience is a chatbot that answers like any generic assistant,
because that is precisely what it is — the general assistant, with nothing to
route to and nothing to retrieve. Nothing on screen explains that the app only
becomes itself once you have a specialist agent, with a description, trained on
a document.

The failure is not that the interface is confusing. It is that **the app looks
finished while doing nothing useful**, and a new user has no way to tell the
difference between "working" and "not set up yet".

## What this adds

A **Getting started** panel at the top of the dashboard: five steps, each
ticked from the account's real data. It shows itself while there is still
something to do, and a **Help** button in the dashboard header brings it back
at any time — including long after all five are done.

Dashboard only. That is where the journey starts and where four of the five
actions begin.

## Why a derived checklist rather than a tour

Three formats were considered.

A **guided tour** with coach marks teaches where the buttons are. The buttons
are not the problem — an empty chatbot with a well-labelled "Manage agents"
button is still an empty chatbot. A tour also advances when you click Next,
whether or not you did anything, so it can report success for an account that
is still empty.

A **pre-built example chatbot** would be the most convincing, but it picks
content for the user, costs extraction and indexing on every signup, and leaves
a knowledge base per account that most people never delete.

A **checklist derived from real data** teaches by doing, cannot report progress
that did not happen, and doubles as a diagnosis for an account that stalled
halfway. That is the choice here.

## The five steps

| Step | Derived from | Why it earns a place |
|---|---|---|
| Create a chatbot | the user owns ≥ 1 chatbot | Pre-ticked at signup — a done item shows what done looks like |
| Add a specialist agent | the user owns ≥ 1 agent | — |
| Give it a description | ≥ 1 agent with a non-empty `description` | The single most common silent failure |
| Train it on a document | ≥ 1 agent with `kb_id` or `kb_full_id`, or ≥ 1 chatbot with either | — |
| Ask a question it can answer | ≥ 1 message with `answered_by_id` not null | The only step that proves the app worked |

### Two of these carry the whole feature

**"Give it a description."** Routing is a classifier that matches the user's
message against each agent's description. An agent with an empty description
is never chosen, the general assistant answers instead, and nothing anywhere
says why. Users hit this and conclude the product is broken. Making it a step
turns an invisible failure into a visible checkbox.

**"Ask a question it can answer."** Deliberately not "send a message".
`messages.answered_by_id` is null for user turns *and* for the general
assistant, so this ticks only when a **specialist actually answered** — the
moment routing and retrieval both did their jobs. It is the difference between
"you used the app" and "the app did the thing it exists to do".

Verified against the live project on 2026-08-22: of 51 assistant turns, 20
carry an `answered_by_id` and 31 do not, and every one of the 31 has
`answered_by_name = "General assistant"`. The discriminator is real, not
assumed.

## Derivation, not stored flags

Every step is computed from live data on each request. No `onboarding_step`
columns, no event writes.

The reason is that flags drift from reality. A user who creates an agent and
then deletes it would keep a ticked box for an agent that no longer exists, and
the panel would be lying at exactly the moment the user most needs it to be
honest. Derived state cannot do that: **delete your only agent and the step
un-ticks.**

## The endpoint

```
GET /onboarding   ->   { "steps": [ {id, label, hint, done} ], "complete": bool }
```

Authenticated, scoped to the caller. Four reads, three of which already have
client methods:

- `list_chatbot_rows(owner_id)` — steps 1 and 4
- `list_agent_rows_by_owner(owner_id)` — steps 2, 3 and 4
- `list_sessions_by_owner(owner_id)` — the session ids for step 5
- one new query: `messages` where `session_id in (…)` and `answered_by_id not
  null`, `limit 1`

The last is a single PostgREST `in.()` request, not one per session, and is
**skipped entirely when the user has no sessions** — the common case for the
account that most needs this panel.

`hint` carries the one sentence explaining why a step matters (for the
description step: that routing matches on it). The server owns the copy so the
panel is not a second place the same explanation can drift.

## Showing, hiding, and the Help button

A **Help** button sits in `.dashboard__head-actions`, beside the theme toggle
and Log out. It toggles the panel, and it is the only control — the panel's own
close button does the same thing, so there are not two mechanisms to keep in
agreement. It carries `aria-expanded` and `aria-controls` so the relationship
is not conveyed by position alone.

Visibility is one boolean, `helpOpen`, decided at page load and flipped by the
button:

    helpOpen = !complete && !dismissed

Hiding the panel while steps remain sets `dismissed` in `localStorage`, so it
stays out of the way on later visits. Once complete, nothing needs storing —
the panel does not open itself, so there is nothing to suppress.

Dismissal is per-browser, which is correct rather than a compromise. Someone
who dismissed it elsewhere either finished the steps — in which case it stays
shut anyway — or has not, and a reminder on a machine that has never shown one
is the right behaviour.

That means **no migration**.

### Two modes, one panel

The Help button makes the panel outlive its checklist, so it has to read well
when every box is ticked. The steps and their hints are the same; what changes
is how much of the hint text is on screen.

| | Opened by itself (steps remain) | Opened from Help |
|---|---|---|
| Hints | under unfinished steps only | under **every** step |
| Reads as | what to do next | what each part does and why |

A finished step does not need its reason restated while you are working
through the list — but that reason is exactly what you came back for when you
press Help. Same five steps, same server-owned copy, one flag deciding how much
of it renders.

When the panel is opened from Help with everything done, it leads with a single
line — "All set. Here is what each part does." — so a wall of ticks reads as a
reference rather than a checklist with nothing left in it.

## The panel

Above the dashboard grid, styled from the existing custom properties so it
follows the theme. A heading, a close button, and five rows: a tick or an empty
box, the label, and the hint — on every step in help mode, on unfinished steps
only otherwise.

Rendering is governed by `helpOpen` alone, per the section above; the panel
itself holds no second opinion about when it should be on screen. It does not
block, cover or dim anything, and the dashboard behaves identically with it
present — which is what lets Help open it over a working dashboard without
taking the page over.

## Testing

Unit tests over the derivation, covering the states that actually distinguish a
working implementation:

- a fresh account: only step 1 ticked;
- an agent with an empty description: step 2 ticked, step 3 not;
- an agent with a description but no knowledge: step 4 not ticked;
- **a chat where only the general assistant answered: step 5 must stay
  unticked** — this is the test that separates "sent a message" from "routing
  worked", and would pass wrongly against an implementation that only counted
  messages;
- everything done: `complete` is true;
- an agent deleted after the fact: the step un-ticks.

Plus a route test proving another user's chatbots, agents and messages never
count toward the caller's progress.

DOM tests in the existing jsdom harness for the toggle, which is where the
mistakes will be:

- Help opens the panel when the account is complete — the case the panel's own
  auto-show logic deliberately refuses, so it is the one most easily broken by
  a stray `complete` check in the render path;
- Help re-opens it after dismissal, in the same page load;
- opening from Help renders a hint under a **completed** step, and opening it
  automatically does not — the visible difference between the two modes;
- hiding while steps remain writes the dismissal flag; hiding when complete
  writes nothing;
- `aria-expanded` tracks the panel across a toggle in both directions.

The harness cannot model the `[hidden]` cascade — a documented trap in
`tools/domtest/README.md` — so panel visibility is asserted on the attribute
and on a static cascade audit, never on `getComputedStyle`.

## Out of scope

- Coach marks, tooltips, overlays, or anything that dims the page.
- A "more to explore" list, and steps for sharing, per-chat agent scope,
  context budget, reasoning effort, or promoting a chat upload. They are
  discoverable once the basics work and meaningless before.
- Any tutorial content inside a chatbot, or on the public share page.
- A Help control anywhere but the dashboard header, and any second
  help surface (docs page, FAQ, searchable help).
