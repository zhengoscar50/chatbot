// The dashboard: a card per chatbot, each listing its agents.
//
// Every screen transition goes through showDashboard() or enterChatbot() —
// never by setting `hidden` at a call site. Three sections toggled by a boolean
// is simple until one path forgets a toggle and two screens render at once, so
// there is exactly one place for that bug to live.

// Which chatbot you are currently INSIDE. sessionStorage, not localStorage, on
// purpose: a refresh keeps your place, a new tab starts at the dashboard.
const CHATBOT_SESSION_KEY = "rag-chat-inside";

const dashboard = document.getElementById("dashboard");
const dashboardGrid = document.getElementById("dashboard-grid");
const dashboardStatus = document.getElementById("dashboard-status");
const appView = document.getElementById("app-view");

function wireDashboard() {
  document.getElementById("to-dashboard").addEventListener("click", showDashboard);
  document.getElementById("dashboard-logout").addEventListener("click", doLogout);
  document.addEventListener("click", closeAllCardMenus);
  wireShare();
  wireOnboarding();
}

const shareModal = document.getElementById("share-modal");
let sharingBot = null;

async function openShare(bot) {
  sharingBot = bot;
  // Blank the fields BEFORE the modal is visible. It used to appear already
  // populated with the PREVIOUS chatbot's link and stay that way for a whole
  // network round trip — long enough to copy the wrong link and be told it
  // worked. A share link that silently belongs to another chatbot is worse
  // than no link at all.
  setShareFields(null, "Loading…");
  shareModal.hidden = false;
  await refreshShare();
}

// One place that decides what the fields hold and whether copying is allowed,
// so "no link yet" and "link loaded" cannot disagree.
function setShareFields(state, usageText) {
  const url = (state && state.url) || "";
  const embed = (state && state.embed) || "";
  document.getElementById("share-url").value = url;
  document.getElementById("share-embed").value = embed;
  document.getElementById("share-usage").textContent = usageText;
  document.getElementById("share-copy").disabled = !url;
  document.getElementById("share-embed-copy").disabled = !embed;
}

async function refreshShare() {
  const res = await authFetch(`/chatbots/${encodeURIComponent(sharingBot.id)}/share`);
  if (!res.ok) {
    document.getElementById("share-usage").textContent = "Could not load sharing.";
    return;
  }
  paintShare(await res.json());
}

function paintShare(state) {
  const on = Boolean(state.token);
  setShareFields(state, on
    ? `${state.used_today} / ${state.daily_limit} messages used today`
    : "Not shared yet.");
  document.getElementById("share-regenerate").textContent =
    on ? "Regenerate link" : "Create link";
  document.getElementById("share-stop").hidden = !on;
  shareIsLive = on;
}

// Whether the chatbot currently in the modal already has a live link, so
// "Regenerate" knows it is about to destroy one.
let shareIsLive = false;

function wireShare() {
  document.getElementById("share-close").addEventListener("click", () => {
    shareModal.hidden = true;
  });
  document.getElementById("share-regenerate").addEventListener("click", async () => {
    // Creating a first link is harmless; replacing a live one is not — anyone
    // already holding it loses access with no notice. Same button, very
    // different consequence, so only the destructive case asks.
    if (shareIsLive &&
        !confirm("Replace this link? Anyone already using the current one will lose access.")) {
      return;
    }
    const res = await authFetch(
      `/chatbots/${encodeURIComponent(sharingBot.id)}/share`, { method: "POST" });
    if (res.ok) { paintShare(await res.json()); await loadDashboard(); }
  });
  document.getElementById("share-stop").addEventListener("click", async () => {
    // Regenerating leaves the old link dead; stopping leaves no link at all.
    if (!confirm("Stop sharing? The existing link will stop working.")) return;
    const res = await authFetch(
      `/chatbots/${encodeURIComponent(sharingBot.id)}/share`, { method: "DELETE" });
    if (res.ok) { paintShare(await res.json()); await loadDashboard(); }
  });
  // The confirmation lands on the button itself, not on #share-usage — that
  // line is showing "N / limit used today", and "Copied." replacing it would
  // hide the usage count until the modal is reopened.
  [["share-copy", "share-url"], ["share-embed-copy", "share-embed"]].forEach(
    ([button, field]) => {
      const btn = document.getElementById(button);
      const original = btn.textContent;
      document.getElementById(button).addEventListener("click", async () => {
        const el = document.getElementById(field);
        if (!el.value) return;          // nothing loaded yet; the button is disabled anyway
        el.select();
        // AWAIT the write and report what actually happened. Reporting
        // "Copied." regardless meant a failed write left the previous
        // clipboard contents in place while the UI said otherwise — you
        // paste a stale link and blame the link.
        let ok = true;
        try {
          await navigator.clipboard.writeText(el.value);
        } catch (err) {
          ok = false;
        }
        btn.textContent = ok ? "Copied." : "Press \u2318C to copy";
        setTimeout(() => {
          btn.textContent = original;
        }, ok ? 1500 : 3000);
      });
    });
}

function closeAllCardMenus() {
  dashboardGrid.querySelectorAll(".bot-card__actions").forEach((el) => {
    el.hidden = true;
  });
}

async function showDashboard() {
  // Leaving a chatbot clears the resume marker, so the next refresh stays here
  // rather than bouncing back into the chatbot you deliberately left.
  sessionStorage.removeItem(CHATBOT_SESSION_KEY);
  currentChatbotId = null;
  currentSessionId = null;
  appView.hidden = true;
  dashboard.hidden = false;
  // Modals are siblings of #app-view, not children, so hiding it alone leaves
  // one floating over the dashboard grid — close whatever might be open.
  agentModal.hidden = true;
  agentListModal.hidden = true;
  scopeModal.hidden = true;
  knowledgeModal.hidden = true;
  shareModal.hidden = true;
  await loadDashboard();
}

// A 404 from an agents/sessions load scoped to `currentChatbotId` means the
// chatbot itself is gone (deleted in another tab, most often) — the server
// makes no other 404 for that query. Bounce back to the dashboard rather than
// leaving the chat view stranded on a chatbot that no longer exists.
async function handleChatbotGone() {
  await showDashboard();
  dashboardStatus.textContent = "That chatbot no longer exists.";
}

async function enterChatbot(id) {
  const bot = chatbots.find((c) => c.id === id);
  if (!bot) {
    // A cheap sanity check against the last-loaded list, not a live check —
    // it only fires when `id` is missing from `chatbots` (a stale resume
    // marker, or any future caller passing an id we never loaded). A card
    // for a chatbot deleted in another tab still passes this find() until
    // this tab's list is refreshed, so this does not, by itself, catch that
    // race. The genuine recovery for that case is elsewhere: enterApp()
    // re-fetches chatbots on every load/refresh, "← Dashboard" stays
    // reachable throughout, and loadAgents()/loadSessions() degrade to a
    // sidebar error rather than crashing if a dead id slips through anyway.
    dashboardStatus.textContent = "That chatbot no longer exists.";
    dashboard.hidden = false;
    appView.hidden = true;
    await loadDashboard();
    return;
  }
  currentChatbotId = id;
  sessionStorage.setItem(CHATBOT_SESSION_KEY, id);
  dashboard.hidden = true;
  appView.hidden = false;
  activeTitle.textContent = bot.name;
  currentSessionId = null;
  clearThread("Pick or create a chat to start.");
  await loadAgents();
  if (currentChatbotId !== id) return;   // loadAgents() already bounced us back
  await loadSessions();
}

const CARD_AGENT_LIMIT = 3;

async function loadDashboard() {
  dashboardStatus.textContent = "Loading…";
  dashboardGrid.innerHTML = "";
  if (!(await loadChatbots())) {
    showDashboardError("Could not load your chatbots.");
    return;
  }
  // Each card needs its own agents and chats. Fanned out rather than awaited
  // in sequence, so one slow chatbot does not hold up the rest of the grid.
  const details = await Promise.all(chatbots.map(loadCardDetail));
  dashboardStatus.textContent = "";
  dashboardGrid.innerHTML = "";
  details.forEach((detail) => dashboardGrid.appendChild(renderCard(detail)));
  dashboardGrid.appendChild(renderNewTile());
  // After the grid, not before: the checklist is a footnote to the dashboard,
  // and its fetch must never delay the cards. Deliberately not awaited.
  refreshOnboarding();
}

function showDashboardError(message) {
  dashboardStatus.textContent = message;
  dashboardGrid.innerHTML = "";
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "bot-card bot-card--new";
  retry.textContent = "Try again";
  retry.addEventListener("click", loadDashboard);
  dashboardGrid.appendChild(retry);
}

function renderNewTile() {
  const tile = document.createElement("button");
  tile.type = "button";
  tile.className = "bot-card bot-card--new";
  tile.textContent = "+ New chatbot";
  tile.addEventListener("click", createChatbotFromDashboard);
  return tile;
}

async function createChatbotFromDashboard() {
  const name = prompt("Name this chatbot");
  if (!name) return;
  const res = await authFetch("/chatbots", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    dashboardStatus.textContent = await detailOf(res, "Could not create that chatbot.");
    return;
  }
  await loadDashboard();
}

async function renameChatbot(bot) {
  const name = prompt("Rename this chatbot", bot.name);
  if (!name || name === bot.name) return;
  const res = await authFetch(`/chatbots/${encodeURIComponent(bot.id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    dashboardStatus.textContent = await detailOf(res, "Could not rename that chatbot.");
    return;
  }
  await loadDashboard();
}

async function deleteChatbotFromDashboard(bot) {
  // Counted only now, not on every card: the document count is the one number
  // that costs a Powabase round trip per knowledge tier.
  let docs = 0;
  try {
    const res = await authFetch(
      `/knowledge/documents?chatbot_id=${encodeURIComponent(bot.id)}`
    );
    if (res.ok) docs = (await res.json()).length;
  } catch (err) {
    docs = 0;
  }
  const roster = await authFetch(
    `/agents?chatbot_id=${encodeURIComponent(bot.id)}`
  ).then((r) => (r.ok ? r.json() : [])).catch(() => []);
  const chats = await authFetch(
    `/sessions?chatbot_id=${encodeURIComponent(bot.id)}`
  ).then((r) => (r.ok ? r.json() : [])).catch(() => []);

  const message =
    `Delete "${bot.name}"?\n\n` +
    `This removes ${roster.length} agents, ${chats.length} chats, ` +
    `and ${docs} documents.\n\nThis can't be undone.`;
  if (!confirm(message)) return;

  const res = await authFetch(`/chatbots/${encodeURIComponent(bot.id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    // The server refuses to delete a user's last chatbot with 400 and a
    // message. Show its message rather than inventing one.
    dashboardStatus.textContent = await detailOf(res, "Could not delete that chatbot.");
    return;
  }
  await loadDashboard();
}

async function detailOf(res, fallback) {
  try {
    const body = await res.json();
    return errorText(body, res) || fallback;
  } catch (err) {
    return fallback;
  }
}

async function loadCardDetail(bot) {
  const q = "?chatbot_id=" + encodeURIComponent(bot.id);
  const get = async (path) => {
    try {
      const res = await authFetch(path + q);
      return res.ok ? await res.json() : [];
    } catch (err) {
      return [];   // a card with no counts beats a grid that fails to draw
    }
  };
  const [agentRows, sessionRows, share, visitors] = await Promise.all([
    get("/agents"),
    get("/sessions"),
    authFetch(`/chatbots/${encodeURIComponent(bot.id)}/share`)
      .then((r) => (r.ok ? r.json() : null)).catch(() => null),
    authFetch(`/sessions?chatbot_id=${encodeURIComponent(bot.id)}&shared=true`)
      .then((r) => (r.ok ? r.json() : [])).catch(() => []),
  ]);
  return { bot, agents: agentRows, chats: sessionRows.length, share, visitors };
}

function renderCard({ bot, agents: roster, chats, share, visitors }) {
  const card = document.createElement("article");
  card.className = "bot-card";
  card.tabIndex = 0;
  card.setAttribute("role", "button");

  const head = document.createElement("header");
  head.className = "bot-card__head";
  const name = document.createElement("h2");
  name.className = "bot-card__name";
  name.textContent = bot.name;
  name.title = bot.name;          // the CSS truncates; the tooltip does not
  head.appendChild(name);

  const menuButton = document.createElement("button");
  menuButton.type = "button";
  menuButton.className = "bot-card__menu";
  menuButton.textContent = "⋯";
  menuButton.setAttribute("aria-label", `Actions for ${bot.name}`);
  head.appendChild(menuButton);
  card.appendChild(head);

  const actions = document.createElement("div");
  actions.className = "bot-card__actions";
  actions.hidden = true;
  actions.addEventListener("click", (event) => {
    event.stopPropagation();   // a click on the menu's own border must not fall through to the card
  });
  [["Share", () => openShare(bot)],
   ["Rename", () => renameChatbot(bot)],
   ["Delete", () => deleteChatbotFromDashboard(bot)]].forEach(([text, run]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.addEventListener("click", (event) => {
      event.stopPropagation();   // the card behind opens the chatbot
      actions.hidden = true;
      run();
    });
    actions.appendChild(b);
  });
  card.appendChild(actions);

  menuButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const wasHidden = actions.hidden;
    closeAllCardMenus();
    actions.hidden = !wasHidden;
  });

  if (bot.description) {
    const desc = document.createElement("p");
    desc.className = "bot-card__desc";
    desc.textContent = bot.description;
    card.appendChild(desc);
  }

  const label = document.createElement("p");
  label.className = "bot-card__label";
  label.textContent = "Agents";
  card.appendChild(label);

  if (roster.length === 0) {
    // An empty region reads as a failed load, so say it plainly instead.
    const none = document.createElement("p");
    none.className = "bot-card__none";
    none.textContent = "No agents yet";
    card.appendChild(none);
  } else {
    const list = document.createElement("ul");
    list.className = "bot-card__agents";
    roster.slice(0, CARD_AGENT_LIMIT).forEach((a) => {
      const li = document.createElement("li");
      li.textContent = a.name;
      list.appendChild(li);
    });
    card.appendChild(list);
    if (roster.length > CARD_AGENT_LIMIT) {
      const more = document.createElement("p");
      more.className = "bot-card__more";
      more.textContent = `+${roster.length - CARD_AGENT_LIMIT} more`;
      card.appendChild(more);
    }
  }

  const count = document.createElement("p");
  count.className = "bot-card__count";
  count.textContent = chats === 1 ? "1 chat" : `${chats} chats`;
  card.appendChild(count);

  if (share && share.token) {
    const shared = document.createElement("p");
    shared.className = "bot-card__shared";
    shared.textContent = `Shared · ${share.used_today}/${share.daily_limit} today`;
    card.appendChild(shared);
  }

  if (visitors && visitors.length > 0) {
    const visited = document.createElement("p");
    visited.className = "bot-card__visitors";
    visited.textContent = `${visitors.length} visitor chats`;
    card.appendChild(visited);
  }

  card.addEventListener("click", () => enterChatbot(bot.id));
  card.addEventListener("keydown", (event) => {
    // Enter/Space bubble up from the card's own child buttons (the ⋯ menu,
    // Rename, Delete). Without this, activating any of them here also
    // re-triggers enterChatbot() on the card underneath — cancelling the
    // button's own action and making those buttons unreachable by keyboard.
    if (event.target !== card) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      enterChatbot(bot.id);
    }
  });
  return card;
}
