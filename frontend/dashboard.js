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
  await loadDashboard();
}

async function enterChatbot(id) {
  const bot = chatbots.find((c) => c.id === id);
  if (!bot) {
    // Deleted in another tab, or a stale resume marker. Redraw rather than
    // entering a chatbot that is not there.
    dashboardStatus.textContent = "That chatbot no longer exists.";
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
  const [agentRows, sessionRows] = await Promise.all([get("/agents"), get("/sessions")]);
  return { bot, agents: agentRows, chats: sessionRows.length };
}

function renderCard({ bot, agents: roster, chats }) {
  const card = document.createElement("article");
  card.className = "bot-card";
  card.dataset.id = bot.id;
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
  [["Rename", () => renameChatbot(bot)],
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

  card.addEventListener("click", () => enterChatbot(bot.id));
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      enterChatbot(bot.id);
    }
  });
  return card;
}
