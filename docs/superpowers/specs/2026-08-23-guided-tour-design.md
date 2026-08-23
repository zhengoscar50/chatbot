# An interactive guided tour

> **Status: draft for review.** Supersedes the "Why a derived checklist rather
> than a tour" section of the 2026-08-22 checklist design, which argued against
> this. That argument lost on one point it got wrong — see *What changed my
> mind* below.

## What this is

A tour that walks a new user through the app by making them use it. Each step
spotlights one real control, explains it in a box beside it, and waits. When
the user clicks the highlighted thing, the real action happens and the tour
advances. A **Next** button moves on without acting; a **Skip** button ends it.

It plays automatically the first time an account reaches the dashboard, and
replays whenever the **?** button is pressed.

By the end, a user who followed it has a chatbot with a described specialist
agent, trained on their own document, that has answered a real question — not a
user who has read about those things.

## What changed my mind

The checklist design rejected a tour on the grounds that "a tour advances when
you click Next, whether or not you did anything, so it can report success for an
account that is still empty."

That is an argument against a *pointing* tour. It is not an argument against a
tour whose steps are satisfied by the real action. This design keeps the honesty
property that made the checklist worth building — **a step is only complete when
the underlying state is actually true** — and adds the thing the checklist could not
do: showing you where the control is and what to type in it.

The other objection was cost, and that one was right. See *What this costs*.

## The tour and the checklist are one mechanism

`GET /onboarding` already derives, from real rows, which of five things an
account has done. That endpoint becomes the tour's brain rather than a second
opinion beside it.

**Each tour step reads its `done` flag and picks a mode:**

| `done` | Mode | Behaviour |
|---|---|---|
| `false` | **Do it** | "Click *Manage agents*." Advances when the action lands. |
| `true` | **Show it** | "This is where your agents live." Advances on Next. |

This is what makes replay coherent. A brand-new account gets the full guided
arc. An account that already has everything presses **?** and gets a tour of
things that exist, with no instruction to create a second copy of anything. One
step list, one source of truth, two voices.

The checklist panel stays exactly as it is — the quiet auto-showing summary for
someone mid-setup who does not want to be walked anywhere. The **?** button,
which currently opens that panel, now starts the tour instead.

## The steps

Eight steps across three surfaces. Each names its target, what completes it, and
what it says.

| # | Target | Completes when | The point |
|---|---|---|---|
| 1 | `#dashboard-grid` | Next | "Each box is a chatbot. Its agents are listed inside." |
| 2 | the first `.bot-card` | the app view opens | Enter a chatbot — where the work happens |
| 3 | `#manage-agents` | `#agent-list-modal` visible | Agents are the specialists |
| 4 | `#agent-list-new` | `#agent-modal` visible | Create one |
| 5 | `#agent-description` | the field has ≥ 1 non-space character | **The step this tour exists for** |
| 6 | `#my-knowledge` | a document appears in the list | Give it something to retrieve |
| 7 | `#chat-input` | a message is sent | Ask something the document covers |
| 8 | the answer's agent badge | Next | A *specialist* answered — that is the app working |

Step 5 carries the feature. Routing matches a question against each agent's
description, so an agent without one is silently never chosen and nothing says
why. A checklist can tell you the field matters. A tour can put the cursor in
it while it says so, which is the whole reason to build this.

Step 8 reads `answered_by_id` — the same discriminator the checklist uses,
already verified against live data in both polarities. If the general assistant
answered, step 8 says so and offers to go back to step 5, because that is
exactly the failure the tour exists to prevent and the user is now looking
straight at it.

## Two calls I made, so the plan has a complete spec to argue from

**Step 8's wrong-answer branch is worth building, as a message and a button —
not an automatic rewind.** If `answered_by_id` is null, the general assistant
answered, and the user is looking straight at the failure this whole tour
exists to prevent. Saying nothing there wastes the best teaching moment the
app will ever get. But silently yanking them back to step 5 would be
disorienting, so step 8 offers "The general assistant answered, not a
specialist — check your agent's description?" with a button back to step 5 and
a Next that accepts it. One branch, no state machine change.

**Eight steps is acceptable because the tour is already resumable.** The
concern is real — eight steps including a document upload is a long first run.
It is mitigated by a property the design already has rather than by cutting
steps: because each step reads its `done` flag from `GET /onboarding`, a user
who bails after step 5 and later presses **?** starts at step 6, since steps
1-5 now derive as done. Abandoning the tour costs nothing, and no step is ever
demanded twice. Skip is visible from step 1 onward, and steps in show-it mode
advance on a single Next, so a partially set-up account moves through the
early steps in seconds.

## How the spotlight works

Four positioned `<div>`s — above, below, left, right of the target's bounding
rect — dim the page and, between them, leave the target genuinely uncovered.

This is deliberately not one overlay with a transparent hole. An overlay covers
the target, so clicks land on the overlay; `pointer-events: none` fixes that but
then lets clicks through *everywhere*, and the user wanders off-script mid-tour.
Four panes make the geometry do the work: the highlighted control is the only
thing on the page that can be clicked, with no event interception at all.

The explanation box is positioned beside the target on the side with room, and
flips when there isn't any. On a narrow screen it docks to the bottom edge and
the panes still dim.

Rects are recomputed on `scroll`, `resize`, and after each advance.

## Waiting, which is where tours break

A step's target frequently does not exist when the step begins. `.bot-card`
elements are built by `renderCard` after two network round trips; `#agent-modal`
exists in the static HTML but is `hidden` until opened.

So every step declares a target and a completion condition, and the engine
polls both on an animation frame with a timeout:

```
waitForElement(selector)   -> present AND visible AND non-zero rect
waitForCondition(fn)       -> the step's own completion test
```

**A step that times out does not hang the tour.** After ~15s it shows "Can't
find that — skip this step?" with Next and Skip. Silence is the failure mode
that makes tours feel broken, so there is no path that produces it.

### Going off-script

The user will cancel the name prompt, close a modal, or hit the back of the
dashboard mid-step. The engine handles exactly three cases, because these are
the ones the real UI produces:

- **The target vanished** (modal closed): re-wait for it, and re-show the step's
  instruction rather than silently sitting there.
- **The user navigated to another surface** (back to the dashboard from inside a
  chatbot): rewind to the last step belonging to the surface they are now on.
- **A prompt was cancelled**: nothing was created, the completion condition is
  still false, so the step simply stands. No special handling needed — this is a
  consequence of testing state rather than clicks.

## Starting and stopping

**On signup.** `frontend/app.js` already branches on `authMode === "register"`
at the `/auth/register` call. A successful register sets a one-shot flag that
the dashboard's first render consumes. No storage involved — a brand-new
account is by definition the first visit.

**On ?**. The button starts the tour instead of toggling the checklist panel.

**Skip** ends the tour and writes `rag-chat-tour-skipped` to `localStorage`, so
autoplay does not fire again on that browser. **?** ignores that flag entirely —
it is an explicit request.

`Escape` skips. Every read and write of `localStorage` is wrapped in try/catch,
per the constraint that a browser with site data blocked must still render the
dashboard.

## Accessibility

The step box is a `role="dialog"` with `aria-live="polite"` so each step is
announced. Focus moves to the box on advance, and the highlighted control is
reachable by Tab from it — the four dimming panes are `aria-hidden`. Under
`prefers-reduced-motion` the panes and box appear without transition.

The tour must not be a keyboard trap: Escape always ends it, from any step.

## Testing

The jsdom harness has no layout engine, so it **cannot** verify that the
spotlight lands on the right pixels. It can verify everything that decides
behaviour, and that is where the bugs will be:

- each step's completion condition fires on the real event and not before;
- a `done: true` step renders in show-it mode and advances on Next; a
  `done: false` step does not advance until its condition is true;
- a vanished target re-waits rather than hanging;
- a timeout offers Skip rather than sitting silent;
- Skip writes the flag; **?** replays despite the flag;
- Escape ends the tour from an arbitrary step;
- the four panes leave exactly the target rect uncovered (geometry is
  arithmetic — testable without layout by stubbing `getBoundingClientRect`);
- autoplay fires once after register and not on subsequent loads.

Mutation checks are required on the two that can be written vacuously: weaken a
completion condition to `true` and the step-gating test must fail; delete the
replay-ignores-flag branch and that test must fail.

**Explicitly not covered by any test:** whether the spotlight looks right, in
either theme, at any window size. That needs a person.

## What this costs

Honestly stated, because the earlier design rejected this partly on cost and
that part was correct.

This is a new subsystem, not a panel: an engine (spotlight geometry, waiting,
step state machine, off-script recovery), a step definition table, and wiring
into three surfaces. Call it 400-500 lines of new frontend plus its tests,
against the checklist's ~180.

It also couples to DOM structure across the whole app. Renaming
`#manage-agents`, or restructuring the agent modal, breaks a tour step — and
breaks it silently, in a flow that only new users see. That is the real ongoing
cost, and the timeout-offers-Skip behaviour above is the mitigation: a broken
step degrades to a skippable one rather than a wall.

**No backend change.** `GET /onboarding` already returns everything the tour
needs. No migration.

## Out of scope

- Tours of sharing, per-chat agent scope, reasoning effort, or context budget.
- Any tour content on the public share page.
- Re-running automatically after a skip.
- Progress persisted mid-tour — leaving the page ends it, and **?** restarts
  from the first incomplete step.
