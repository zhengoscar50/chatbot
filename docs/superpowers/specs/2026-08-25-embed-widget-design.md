# A chat widget for someone else's website

## What exists, and what is missing

Sharing already produces a working public chat: an unlisted `/s/<token>` page
that a stranger can talk to, with document filenames stripped from citations
and a daily cap. The Share dialog hands out an iframe snippet for it.

That snippet is a rectangle. Whoever pastes it has to decide where a 420x640
box lives on their page, and it sits there whether anyone wants it or not.
Nobody embeds support chat that way, because a page has no spare rectangle.

This turns it into a widget: a slim tab on the right edge, and a panel that
slides out when clicked.

## What the host pastes

```html
<script src="https://your-host/widget.js" data-token="…" async></script>
```

Optional attributes, and deliberately few:

| Attribute | Default | |
|---|---|---|
| `data-side` | `right` | `left` for the other edge |
| `data-label` | `Ask` | the vertical text on the tab |
| `data-accent` | the app's accent | one colour, for the tab and the send button |

Nothing else. A widget with a wide configuration surface is a widget whose
appearance can never be changed again without breaking somebody.

## The shape of it

One `<div>` appended to `<body>`, carrying an **open shadow root**. Inside it,
two things: the tab button and a panel holding an `<iframe>`.

The shadow root is not decoration. A widget lands on pages whose CSS you have
never seen, written by people who use `!important` and `* { box-sizing }` and
`div { margin: 0 }`. Without it, the tab is one stylesheet away from being
invisible on somebody's site, and your own rules leak out and break their
layout. Shadow DOM makes both directions impossible.

The panel's *contents* stay in an iframe on your origin. That is the second
wall: the chat renders documents and model output, and none of that should
execute inside a stranger's page where it can read their cookies. The host
gets a tab they cannot break; you get a chat they cannot read.

**Collapsed**, the panel is translated off-screen rather than hidden, so
opening slides instead of snapping. The tab rides the panel's edge and moves
with it, doubling as the close control — one object that opens and shuts, not a
button plus a separate dismiss.

## Who owns the session

**The loader, not the panel.** This is the security-relevant decision in the
whole design.

The loader creates the session on **first open**, stores its id, and passes it
to the panel in the iframe URL fragment. The panel reads the fragment and
resumes.

The alternative — the panel creating its own session and posting the id out to
the parent — means sending a session identifier to a frame whose origin you
cannot verify from inside. Fragment-passing means the only things crossing the
boundary are `ready` and `close`, and neither is worth stealing.

**On first open, not on page load.** A visitor who never clicks the tab should
cost nothing: no session row, no request. On a site with traffic, creating a
session per page view would fill the owner's `sessions` table with empty
conversations nobody had.

### The message protocol

Three messages, all from panel to loader:

- `{ source: "powabase-widget", type: "ready" }`
- `{ source: "powabase-widget", type: "close" }`
- `{ source: "powabase-widget", type: "reset" }`

`reset` was added when the panel gained a "start a new conversation" control.
It has to travel this way for the same reason the loader owns the session at
all: the loader holds the stored id, so a panel that reset itself would leave
that id behind and the two would then disagree about which conversation is
current. Like the other two it carries nothing worth stealing — it says only
"throw mine away", and any page that can send it could equally close the panel
or reload it.

The loader ignores any message whose `event.origin` is not the origin of its
own `<script src>`, and any message without that exact `source` field. Both
checks matter: origin alone still lets a different widget on the same host
confuse it.

Nothing is sent loader → panel. The fragment carries everything the panel needs
before it loads.

## Persistence

Two keys in the visitor's `localStorage`, namespaced by token so two widgets on
one page cannot collide:

- the session id, so a thread survives navigating the host site
- whether the panel was open, so it reopens where they left it

Both reads and writes wrapped in try/catch. A browser with site data blocked
must still show a working widget — it simply starts a new conversation each
page, which is the correct degradation.

A stored session id that the server no longer recognises produces a 404 on
resume. The loader discards the id and creates a fresh session rather than
showing the visitor an error about a conversation they cannot see.

## The transcript endpoint

Resuming needs something that does not exist yet: a way for a visitor to read
back their own conversation.

```
GET /s/{token}/session/{session_id}/messages
```

It applies the **same double check** `public_chat` already makes (`share.py`,
the `chatbot_id` and `shared` conditions) — the session
must belong to *this* chatbot **and** carry `shared` — because the owner's own
private chats live in the same chatbot, and membership alone would let a
visitor name one.

It returns 404 on either failure, never 403. A 403 would confirm that a session
exists, which is exactly what an enumeration attempt wants to learn.

**And it redacts.** Every assistant turn goes back through the same
`redact_turn` the live answer path uses. Stored messages are unredacted:
`share.py:127` redacts the *response*, after `answer_turn` has already written
the row, so the database keeps the filename and the visitor never sees it — so an endpoint that
replayed them raw would hand back the document filenames the live path
deliberately stripped. The same answer would be secret when first given and
public when read back an hour later. That is the kind of defect nobody notices
until a filename shows up somewhere it should not.

## On a phone

Below 640px the panel goes full width and the tab becomes a pill in the bottom
corner. A 40px-wide vertical tab is not a touch target, and a 400px panel on a
390px screen is a horizontal scrollbar.

## Testing

**The loader**, in the existing jsdom harness:

- the shadow root is built and the tab is inside it, not in the host document;
- clicking the tab opens the panel, clicking it again closes;
- no session is created until the first open — a page load alone makes no
  request;
- the session id and open state survive a reload;
- a `postMessage` from the wrong origin is ignored, and so is one from the
  right origin without the `source` field;
- storage that throws leaves a working widget.

**The transcript endpoint**, in pytest:

- a session from another chatbot: 404;
- a session in this chatbot that is not `shared` — the owner's own private
  chat: 404;
- citations come back with excerpts and **no filename**, matching what the live
  path returns for the same turn.

That last one is the check worth writing first: it is the one that fails if
somebody later "simplifies" the endpoint into a plain message list.

**Cross-origin, by hand**: the embed-test site already running on port 4173 is
a real second origin. The widget has to work there, in both themes, on a narrow
window.

## What this does not do

- Deleting a visitor's transcript from the owner's data. "New conversation"
  gives the visitor a clean slate; the old rows stay. Promising an erasure this
  cannot deliver would be worse than not offering one.
- Any way for a visitor to promote an attachment into the chatbot's permanent
  knowledge. The account app offers that; a stranger on someone else's website
  must not have it.
- Theming beyond a single accent colour.
- Unread badges, proactive messages, "we're away" states.
- More than one widget on a page.
- Any analytics.
- Replacing the plain iframe snippet, which stays for anyone who wants to place
  a rectangle themselves.
