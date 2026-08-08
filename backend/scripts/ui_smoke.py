"""Drive the real UI in a browser — the layer API tests cannot reach.

Every bug this has caught lived only here: the transcript renderer dropping
attribution badges on reload, and copy left stale by a model change. Neither
was reachable from the API.

    pip install playwright && playwright install chromium
    python scripts/ui_smoke.py

Set URL/INVITE below for the deployment under test.
"""
import time

from playwright.sync_api import sync_playwright

URL = "https://resolution-october-kevin-cities.trycloudflare.com"
INVITE = "rag-iOChsleYQZGV"
SHOTS = "/private/tmp/claude-501/-Users-oscar/4512c8da-fd97-4b9a-b3a7-cb89bee117e9/scratchpad/"
PDF = SHOTS + "board2.pdf"
stamp = str(int(time.time()))

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + label + (("  :: " + str(detail)) if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text))
            if m.type == "error" else None)

    # --- load + register ----------------------------------------------------
    page.goto(URL, wait_until="networkidle")
    page.screenshot(path=SHOTS + "ui1_login.png")
    check("app loads and shows the login gate", page.is_visible("#auth-gate"))

    page.click("#auth-toggle")                       # switch to register
    page.wait_for_timeout(400)
    invite_visible = page.is_visible("#auth-invite")
    check("invite field appears in register mode", invite_visible)
    page.screenshot(path=SHOTS + "ui2_register.png")

    page.fill("#auth-username", "ui-" + stamp)
    page.fill("#auth-password", "probe-pw-12345")
    if invite_visible:
        page.fill("#auth-invite", INVITE)
    page.click("#auth-submit")
    page.wait_for_timeout(4000)
    check("registration signs the user in", not page.is_visible("#auth-gate"))
    page.screenshot(path=SHOTS + "ui3_app.png")

    # --- create an agent through the modal ---------------------------------
    page.click("#manage-agents")
    page.wait_for_timeout(700)
    check("Manage agents opens the list", page.is_visible("#agent-list-modal"))
    page.screenshot(path=SHOTS + "ui4_agent_list.png")

    page.click("#agent-list-new")
    page.wait_for_timeout(700)
    check("New agent opens the form", page.is_visible("#agent-modal"))

    models = page.eval_on_selector_all("#agent-model-select option", "els => els.map(e => e.value)")
    check("model dropdown is populated from /models", len(models) > 3, models[:4] + ["..."])
    check("...and offers an Other option", "__other__" in models)

    page.fill("#agent-name", "Board notes")
    page.fill("#agent-description", "Answers questions about our board meeting notes, dates, rooms and budgets.")
    page.fill("#agent-instructions", "Be concise.")
    page.select_option("#agent-grounding", "strict")
    page.screenshot(path=SHOTS + "ui5_agent_form.png")
    page.click("#agent-save")
    page.wait_for_timeout(9000)          # includes the live model probe
    check("agent saves and the form closes", not page.is_visible("#agent-modal"))
    page.screenshot(path=SHOTS + "ui6_after_save.png")

    # --- train it through the manage view ----------------------------------
    page.click("#manage-agents")
    page.wait_for_timeout(900)
    rows = page.eval_on_selector_all("#agent-list li strong", "els => els.map(e => e.textContent)")
    check("the new agent is listed", "Board notes" in rows, rows)
    descs = page.eval_on_selector_all("#agent-list li .muted", "els => els.map(e => e.textContent)")
    check("...with its description shown", any("board meeting" in (d or "").lower() for d in descs), descs)

    page.click("#agent-list li button")   # Edit
    page.wait_for_timeout(900)
    check("edit reopens the form with the description", "board meeting" in page.input_value("#agent-description").lower())
    page.set_input_files("#agent-train-file", PDF)
    for _ in range(30):
        page.wait_for_timeout(2000)
        if "Trained on" in (page.text_content("#agent-train-status") or ""):
            break
    status = page.text_content("#agent-train-status")
    check("training a PDF reports success", "Trained on" in (status or ""), status)
    docs = page.eval_on_selector_all("#agent-doc-list li", "els => els.map(e => e.textContent)")
    check("...and the document is listed by name", any("board2.pdf" in (d or "") for d in docs), docs)
    page.screenshot(path=SHOTS + "ui7_trained.png")
    page.click("#agent-cancel")
    page.wait_for_timeout(500)

    # --- chat ---------------------------------------------------------------
    page.fill("#chat-input", "What is the approved budget ceiling?")
    page.click("#send-button")
    for _ in range(40):
        page.wait_for_timeout(1500)
        if page.query_selector(".agent-badge"):
            break
    answer = page.text_content(".row--assistant .content") or ""
    badge = page.text_content(".agent-badge") if page.query_selector(".agent-badge") else None
    check("chat returns an answer", "47" in answer, answer[:90])
    check("...with an attribution badge naming the agent", badge == "Board notes", badge)
    page.screenshot(path=SHOTS + "ui8_chat.png", full_page=True)

    # --- persistence across reload -----------------------------------------
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(3500)
    sessions = page.eval_on_selector_all("#session-list li", "els => els.length")
    check("the chat survives a reload", sessions >= 1, "%d chats listed" % sessions)
    page.click("#session-list li")
    page.wait_for_timeout(3000)
    reloaded = page.text_content("#messages") or ""
    check("...and the transcript comes back", "47" in reloaded, reloaded[-90:].strip())
    check("...with the badge still attached", "Board notes" in reloaded)
    page.screenshot(path=SHOTS + "ui9_reloaded.png", full_page=True)

    check("no uncaught JS errors on any screen", not errors, errors[:3])
    browser.close()

print("\n%d/%d passed" % (sum(1 for _, ok in RESULTS if ok), len(RESULTS)))
