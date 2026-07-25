# Admin General Knowledge (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-curated shared "general knowledge" base that every new session's agent also searches (alongside that session's own uploads), with an admin-password-gated `/admin` page to upload general-knowledge PDFs.

**Architecture:** A single `general-knowledge-kb` is get-or-created at startup and its id stored on `app.state`; `SessionService` links it into each new session's agent. An `ADMIN_PASSWORD` (optional config) gates two new routes — `/admin/verify` and `/admin/train` (ingest into the general KB) — plus a `/admin` page. Everything else (per-session isolation) is unchanged.

**Tech Stack:** Python 3.9 (env has 3.9.6; new files with module-level `X | None` need `from __future__ import annotations`), FastAPI, httpx, pytest. Plain HTML/JS/CSS frontend, no build step.

## Global Constraints

- Retrieval model: a session answers from **general knowledge + that session's own uploads**. A new session's agent links BOTH its session KB and the general KB.
- Scope: **new sessions only** — do NOT back-fill the general KB into pre-existing sessions.
- The general KB is named `general-knowledge-kb`, get-or-created by name, and always exists (empty until trained). Read the KB list under the `knowledge_bases` key (not `items`).
- `ADMIN_PASSWORD` is optional. If unset, admin endpoints return `403` ("admin not configured") and the app still starts normally.
- Admin password is checked server-side with `hmac.compare_digest`. It is sent with each admin request (demo-grade gate, not hardened auth); the Service Role key stays server-side.
- New files using `X | None` at module/class level start with `from __future__ import annotations`.
- Tests use faked clients/services (no network); the live admin proof is the final task.
- Keep the suite green between tasks.

---

### Task 1: General KB helper + SessionService links it

**Files:**
- Create: `backend/app/services/general_kb.py`
- Modify: `backend/app/services/session_service.py`
- Test: `backend/tests/unit/test_general_kb.py`
- Test: `backend/tests/unit/test_session_service.py` (add cases)

**Interfaces:**
- Produces: `GENERAL_KB_NAME` (str), `ensure_general_kb(client) -> str` (find-or-create the general KB, return its id), `get_general_kb_id(request) -> str` (dependency reading `app.state.general_kb_id`). `SessionService.__init__(client, model, general_kb_id=None)`; `create_session` links the general KB into the agent when `general_kb_id` is set. Consumed by Tasks 2–3.

- [ ] **Step 1: Write the failing tests for `general_kb`**

```python
# backend/tests/unit/test_general_kb.py
from app.services.general_kb import GENERAL_KB_NAME, ensure_general_kb


class FakeClient:
    def __init__(self, existing=None):
        self.kbs = list(existing or [])
        self.created = []

    def list_knowledge_bases(self):
        return {"knowledge_bases": self.kbs}

    def create_knowledge_base(self, name, description=""):
        kb = {"id": f"kb-{name}", "name": name}
        self.kbs.append(kb)
        self.created.append(kb)
        return kb


def test_ensure_general_kb_creates_when_absent():
    client = FakeClient()
    kb_id = ensure_general_kb(client)
    assert kb_id == f"kb-{GENERAL_KB_NAME}"
    assert client.created and client.created[0]["name"] == GENERAL_KB_NAME


def test_ensure_general_kb_reuses_when_present():
    client = FakeClient(existing=[{"id": "kb-existing", "name": GENERAL_KB_NAME}])
    kb_id = ensure_general_kb(client)
    assert kb_id == "kb-existing"
    assert client.created == []
```

- [ ] **Step 2: Run to verify it fails**

Run (from `backend/`): `pytest tests/unit/test_general_kb.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.services.general_kb'`).

- [ ] **Step 3: Write `backend/app/services/general_kb.py`**

```python
from fastapi import Request

GENERAL_KB_NAME = "general-knowledge-kb"


def _find_by_name(items: list, name: str):
    return next((item for item in items if item.get("name") == name), None)


def ensure_general_kb(client) -> str:
    """Find-or-create the shared general-knowledge KB; return its id."""
    existing = client.list_knowledge_bases().get("knowledge_bases", [])
    kb = _find_by_name(existing, GENERAL_KB_NAME)
    if kb is None:
        kb = client.create_knowledge_base(
            GENERAL_KB_NAME, description="Shared admin-curated general knowledge"
        )
    return kb["id"]


def get_general_kb_id(request: Request) -> str:
    """FastAPI dependency returning the general KB id resolved at startup."""
    return request.app.state.general_kb_id
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_general_kb.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Add the `general_kb_id` linking to `SessionService`**

In `backend/app/services/session_service.py`, change `__init__` to accept `general_kb_id` and store it:

```python
    def __init__(self, client, model: str, general_kb_id: str | None = None):
        self.client = client
        self.model = model
        self.general_kb_id = general_kb_id
```

And in `create_session`, right after the existing
`self.client.link_kb_to_agent(agent["id"], kb["id"])` line, add:

```python
        if self.general_kb_id:
            self.client.link_kb_to_agent(agent["id"], self.general_kb_id)
```

- [ ] **Step 6: Add SessionService tests for the general-KB link**

Append to `backend/tests/unit/test_session_service.py`:

```python
def test_create_session_links_general_kb_when_set():
    client = FakeClient()
    service = SessionService(client, model="m", general_kb_id="gkb-1")

    row = service.create_session("alice")

    # Agent linked to BOTH its own session KB and the general KB.
    assert (row["agent_id"], row["kb_id"]) in client.links
    assert (row["agent_id"], "gkb-1") in client.links
    assert len(client.links) == 2


def test_create_session_links_only_session_kb_when_general_none():
    client = FakeClient()
    service = SessionService(client, model="m")  # general_kb_id defaults to None

    row = service.create_session("alice")

    assert client.links == [(row["agent_id"], row["kb_id"])]
```

- [ ] **Step 7: Run the session-service + general-kb tests**

Run: `pytest tests/unit/test_session_service.py tests/unit/test_general_kb.py -v`
Expected: PASS (existing session tests + 2 new session tests + 2 general-kb tests).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/general_kb.py backend/app/services/session_service.py backend/tests/unit/test_general_kb.py backend/tests/unit/test_session_service.py
git commit -m "feat: general-knowledge KB helper; SessionService links it into new agents"
```

---

### Task 2: Provision the general KB at startup

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Consumes: `ensure_general_kb` (Task 1), `SessionService(client, model, general_kb_id)` (Task 1).
- Produces: `app.state.general_kb_id`; `SessionService` on `app.state` is now constructed with the general KB id.

- [ ] **Step 1: Update the lifespan success test**

In `backend/tests/unit/test_main_lifespan.py`, in `test_app_starts_when_powabase_reachable`, add a monkeypatch for `ensure_general_kb` (so startup doesn't hit the network for the KB) before `create_app()`:

```python
    monkeypatch.setattr(main_module, "ensure_general_kb", lambda client: "gkb-1")
```

and add this assertion inside the `with TestClient(app)` block:

```python
        assert app.state.general_kb_id == "gkb-1"
        assert app.state.session_service.general_kb_id == "gkb-1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_main_lifespan.py -v`
Expected: FAIL (`AttributeError: module 'app.main' has no attribute 'ensure_general_kb'`, or missing `general_kb_id`).

- [ ] **Step 3: Update `backend/app/main.py`**

Add the import (next to the SessionService import):

```python
from app.services.general_kb import ensure_general_kb
```

In `lifespan`, replace the block that sets `app.state.session_service` so it reads:

```python
        app.state.powabase_client = client
        general_kb_id = ensure_general_kb(client)
        app.state.general_kb_id = general_kb_id
        app.state.session_service = SessionService(
            client, settings.powabase_agent_model, general_kb_id
        )
        yield
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_main_lifespan.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/unit/test_main_lifespan.py
git commit -m "feat: provision the general-knowledge KB at startup"
```

---

### Task 3: Config + admin routes (verify, train, /admin)

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Modify: `backend/app/models/schemas.py`
- Create: `backend/app/api/routes/admin.py`
- Create: `frontend/admin.html` (placeholder — real page in Task 4; needed so `GET /admin` resolves)
- Modify: `backend/app/main.py` (register the admin router)
- Test: `backend/tests/unit/test_routes_admin.py`

**Interfaces:**
- Consumes: `get_powabase_client`, `get_general_kb_id` (Task 1), `IngestService`, `get_settings`, `FRONTEND_DIR`.
- Produces: `AdminVerifyRequest`; `POST /admin/verify`, `POST /admin/train`, `GET /admin`.

- [ ] **Step 1: Add `admin_password` to `backend/app/core/config.py`**

Add this field to `Settings` (after `powabase_agent_model`):

```python
    admin_password: Optional[str] = None
```

(`Optional` is already imported.)

- [ ] **Step 2: Add `ADMIN_PASSWORD` to `backend/.env.example`**

Append:

```
ADMIN_PASSWORD=
```

- [ ] **Step 3: Add `AdminVerifyRequest` to `backend/app/models/schemas.py`**

Append:

```python
class AdminVerifyRequest(BaseModel):
    password: str = Field(..., min_length=1)
```

- [ ] **Step 4: Write the failing tests**

```python
# backend/tests/unit/test_routes_admin.py
import io

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import admin as admin_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.general_kb import get_general_kb_id


def set_admin(monkeypatch, password="s3cret"):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    if password is None:
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("ADMIN_PASSWORD", password)
    get_settings.cache_clear()


class FakeIngestService:
    def __init__(self, client, kb_id, poll_interval, max_wait):
        assert kb_id == "gkb-1"  # trains into the GENERAL KB

    def ingest_pdf(self, filename, content):
        return {"source_id": "src-1", "status": "indexed"}


def build_app():
    app = FastAPI()
    app.include_router(admin_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_general_kb_id] = lambda: "gkb-1"
    return app


def train(client, password="s3cret"):
    return client.post(
        "/admin/train",
        data={"password": password},
        files={"file": ("g.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )


def test_verify_ok(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_app()).post("/admin/verify", json={"password": "s3cret"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_verify_wrong_password(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_app()).post("/admin/verify", json={"password": "nope"})
    assert r.status_code == 401


def test_verify_not_configured(monkeypatch):
    set_admin(monkeypatch, password=None)
    r = TestClient(build_app()).post("/admin/verify", json={"password": "anything"})
    assert r.status_code == 403


def test_train_ingests_into_general_kb(monkeypatch):
    set_admin(monkeypatch)
    monkeypatch.setattr(admin_route, "IngestService", FakeIngestService)
    r = train(TestClient(build_app()))
    assert r.status_code == 200
    assert r.json() == {"source_id": "src-1", "status": "indexed"}


def test_train_rejects_wrong_password(monkeypatch):
    set_admin(monkeypatch)
    monkeypatch.setattr(admin_route, "IngestService", FakeIngestService)
    r = train(TestClient(build_app()), password="nope")
    assert r.status_code == 401


def test_train_403_when_not_configured(monkeypatch):
    set_admin(monkeypatch, password=None)
    monkeypatch.setattr(admin_route, "IngestService", FakeIngestService)
    r = train(TestClient(build_app()))
    assert r.status_code == 403


def test_train_202_on_timeout(monkeypatch):
    set_admin(monkeypatch)

    class TimeoutService(FakeIngestService):
        def ingest_pdf(self, filename, content):
            raise admin_route.IngestTimeoutError("src-3", "pending")

    monkeypatch.setattr(admin_route, "IngestService", TimeoutService)
    r = train(TestClient(build_app()))
    assert r.status_code == 202
    assert r.json() == {"source_id": "src-3", "status": "pending"}


def test_admin_page_served(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_app()).get("/admin")
    assert r.status_code == 200
```

- [ ] **Step 5: Run to verify it fails**

Run: `pytest tests/unit/test_routes_admin.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.api.routes.admin'`).

- [ ] **Step 6: Write `backend/app/api/routes/admin.py`**

```python
import hmac

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import FRONTEND_DIR, get_settings
from app.models.schemas import AdminVerifyRequest, IngestResponse
from app.services.general_kb import get_general_kb_id
from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
)

router = APIRouter(tags=["admin"])


def _require_admin(password: str) -> None:
    configured = get_settings().admin_password
    if not configured:
        raise HTTPException(status_code=403, detail="Admin is not configured (set ADMIN_PASSWORD).")
    if not hmac.compare_digest(password, configured):
        raise HTTPException(status_code=401, detail="Incorrect admin password.")


@router.post("/admin/verify")
def admin_verify(req: AdminVerifyRequest):
    _require_admin(req.password)
    return {"ok": True}


@router.post("/admin/train", response_model=IngestResponse)
async def admin_train(
    password: str = Form(...),
    file: UploadFile = File(...),
    client: PowabaseClient = Depends(get_powabase_client),
    general_kb_id: str = Depends(get_general_kb_id),
):
    _require_admin(password)
    content = await file.read()
    settings = get_settings()
    service = IngestService(
        client,
        general_kb_id,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_max_wait_seconds,
    )
    try:
        result = await run_in_threadpool(service.ingest_pdf, file.filename, content)
        return IngestResponse(**result)
    except AttentionRequiredError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Source {e.source_id} needs OCR re-extraction (low-quality/scanned PDF).",
        )
    except (ExtractionFailedError, IndexingFailedError) as e:
        raise HTTPException(status_code=500, detail=e.message)
    except IngestTimeoutError as e:
        return JSONResponse(
            status_code=202,
            content=IngestResponse(source_id=e.source_id, status=e.status).model_dump(),
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/admin")
def admin_page():
    return FileResponse(str(FRONTEND_DIR / "admin.html"))
```

- [ ] **Step 7: Create the placeholder `frontend/admin.html`**

```html
<!doctype html>
<html lang="en">
  <head><meta charset="utf-8" /><title>Admin</title></head>
  <body><p>Admin — coming soon.</p></body>
</html>
```

- [ ] **Step 8: Register the admin router in `backend/app/main.py`**

Add the import:

```python
from app.api.routes.admin import router as admin_router
```

And in `create_app()`, after `app.include_router(sessions_router)`:

```python
    app.include_router(admin_router)
```

- [ ] **Step 9: Run to verify it passes**

Run: `pytest tests/unit/test_routes_admin.py -v`
Expected: PASS (8 tests).

- [ ] **Step 10: Run the full suite**

Run: `pytest -q`
Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add backend/app/core/config.py backend/.env.example backend/app/models/schemas.py backend/app/api/routes/admin.py frontend/admin.html backend/app/main.py backend/tests/unit/test_routes_admin.py
git commit -m "feat: admin-gated /admin/verify, /admin/train (general KB), and /admin page"
```

---

### Task 4: Admin frontend page

**Files:**
- Modify: `frontend/admin.html` (replace placeholder with the real page)
- Create: `frontend/admin.js`
- Modify: `frontend/index.html` (add an "Admin" link in the sidebar)
- Modify: `frontend/styles.css` (small admin-page + admin-link styles)

**Interfaces:**
- Consumes: `POST /admin/verify`, `POST /admin/train`.
- Produces: nothing downstream.

- [ ] **Step 1: Replace `frontend/admin.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Admin · General Knowledge</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
    <div class="admin-page">
      <header class="admin-head">
        <span class="topbar__mark" aria-hidden="true"></span>
        <h1 class="admin-title">General Knowledge — Admin</h1>
        <a class="admin-back" href="/">← Back to chat</a>
      </header>

      <section class="admin-card" id="gate">
        <p class="admin-hint">Enter the admin password to manage shared general knowledge.</p>
        <form id="gate-form" class="admin-row">
          <input type="password" id="password-input" class="admin-input" placeholder="Admin password" autocomplete="current-password" />
          <button type="submit" class="btn-solid">Unlock</button>
        </form>
        <p class="admin-status" id="gate-status"></p>
      </section>

      <section class="admin-card" id="uploader" hidden>
        <p class="admin-hint">
          Upload PDFs to the shared general knowledge base. Every <strong>new</strong>
          session can then draw on these, alongside its own uploads.
        </p>
        <div class="admin-row">
          <input type="file" id="file-input" accept="application/pdf" />
          <button type="button" id="upload-button" class="btn-solid">Upload</button>
        </div>
        <p class="admin-status" id="upload-status"></p>
      </section>
    </div>
    <script src="/admin.js"></script>
  </body>
</html>
```

- [ ] **Step 2: Create `frontend/admin.js`**

```javascript
const gateForm = document.getElementById("gate-form");
const passwordInput = document.getElementById("password-input");
const gateStatus = document.getElementById("gate-status");
const gate = document.getElementById("gate");
const uploader = document.getElementById("uploader");
const fileInput = document.getElementById("file-input");
const uploadButton = document.getElementById("upload-button");
const uploadStatus = document.getElementById("upload-status");

let adminPassword = null;

gateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = passwordInput.value;
  if (!password) return;
  gateStatus.textContent = "Checking…";
  gateStatus.removeAttribute("data-state");
  try {
    const response = await fetch("/admin/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (response.ok) {
      adminPassword = password;
      gate.hidden = true;
      uploader.hidden = false;
    } else if (response.status === 401) {
      setStatus(gateStatus, "Incorrect password.", "error");
    } else if (response.status === 403) {
      setStatus(gateStatus, "Admin is not configured (set ADMIN_PASSWORD).", "error");
    } else {
      const body = await response.json().catch(() => ({}));
      setStatus(gateStatus, body.detail || response.statusText, "error");
    }
  } catch (err) {
    setStatus(gateStatus, err.message, "error");
  }
});

uploadButton.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) {
    setStatus(uploadStatus, "Choose a PDF first.", "error");
    return;
  }
  if (!adminPassword) return;
  setStatus(uploadStatus, `Uploading and indexing ${file.name}…`, null);
  const formData = new FormData();
  formData.append("password", adminPassword);
  formData.append("file", file);
  try {
    const response = await fetch("/admin/train", { method: "POST", body: formData });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok || response.status === 202) {
      setStatus(uploadStatus, `${file.name}: ${body.status}`, body.status === "indexed" ? "ok" : null);
      fileInput.value = "";
    } else {
      setStatus(uploadStatus, body.detail || response.statusText, "error");
    }
  } catch (err) {
    setStatus(uploadStatus, err.message, "error");
  }
});

function setStatus(el, text, state) {
  el.textContent = text;
  if (state) el.dataset.state = state;
  else delete el.dataset.state;
}
```

- [ ] **Step 3: Add an "Admin" link to `frontend/index.html`**

Inside the `<aside class="sidebar" ...>`, immediately before the closing `</aside>`, add (after the `sidebar-status` paragraph):

```html
        <a class="admin-link" href="/admin">Admin</a>
```

- [ ] **Step 4: Append admin styles to `frontend/styles.css`**

```css
.admin-link {
  font-size: 0.75rem;
  color: var(--text-muted);
  text-decoration: none;
  padding: 0.3rem 0.1rem;
}

.admin-link:hover {
  color: var(--accent-2);
  text-decoration: underline;
}

.admin-page {
  max-width: 40rem;
  margin: 0 auto;
  padding: 2rem 1.25rem;
}

.admin-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 1.5rem;
}

.admin-title {
  font-size: 1.05rem;
  font-weight: 600;
  margin: 0;
  flex: 1 1 auto;
}

.admin-back {
  font-size: 0.85rem;
  color: var(--accent);
  text-decoration: none;
}

.admin-card {
  border: 1px solid var(--border);
  border-radius: 0.9rem;
  padding: 1.1rem 1.25rem;
  margin-bottom: 1rem;
}

.admin-card[hidden] {
  display: none;
}

.admin-hint {
  margin: 0 0 0.8rem;
  color: var(--text-muted);
  font-size: 0.9rem;
}

.admin-row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  flex-wrap: wrap;
}

.admin-input {
  flex: 1 1 12rem;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 0.6rem;
  padding: 0.5rem 0.7rem;
  font: inherit;
  font-size: 0.95rem;
}

.admin-input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.btn-solid {
  border: none;
  border-radius: 0.6rem;
  padding: 0.5rem 1rem;
  font: inherit;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--accent-contrast);
  background: var(--gradient);
  cursor: pointer;
}

.btn-solid:hover {
  filter: brightness(1.06);
}

.admin-status {
  margin: 0.7rem 0 0;
  font-size: 0.85rem;
  color: var(--text-muted);
  min-height: 1em;
}

.admin-status[data-state="ok"] {
  color: var(--ok);
}

.admin-status[data-state="error"] {
  color: var(--error);
}
```

- [ ] **Step 5: Verify syntax and backend suite**

Run: `node -c frontend/admin.js && echo "admin.js OK"` (expect `admin.js OK`).
Run (from `backend/`): `pytest -q` (expect all green — no backend change here).

- [ ] **Step 6: Commit**

```bash
git add frontend/admin.html frontend/admin.js frontend/index.html frontend/styles.css
git commit -m "feat: admin page (password gate + general-knowledge uploader) and sidebar link"
```

---

### Task 5: README + manual verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: docs + a live admin/general-knowledge proof.

- [ ] **Step 1: Update `README.md`**

Add an admin section (after the Sessions section):

```markdown
## Admin: shared general knowledge

Set `ADMIN_PASSWORD` in `backend/.env` to enable the admin feature. Then open
`/admin` (there's an "Admin" link in the sidebar), enter the password, and upload
PDFs into the shared **general knowledge** base.

Every **new** session's chatbot answers from general knowledge **plus** that
session's own uploaded documents. Sessions created before general knowledge was
added keep only their own documents (new-sessions-only). If `ADMIN_PASSWORD` is
not set, the admin endpoints are disabled and the rest of the app runs normally.

Scope note: the admin password is checked server-side but sent with each admin
request — a demo-grade gate, not hardened authentication.
```

- [ ] **Step 2: Run the full backend suite**

Run (from `backend/`): `pytest -q`
Expected: all green.

- [ ] **Step 3: Manual admin/general-knowledge proof (live Powabase)**

Set `ADMIN_PASSWORD` in `backend/.env`, start the app (`uvicorn app.main:app --reload`), then:

- [ ] Open `/admin`, enter the password → unlocked. A wrong password → "Incorrect password"; unset `ADMIN_PASSWORD` → "Admin is not configured".
- [ ] Upload a general-knowledge PDF stating a distinctive fact → indexed.
- [ ] Create a **new** session (new user or new session), and — without uploading
      anything to the session — ask about that fact → it answers from general
      knowledge (with a citation).
- [ ] In the same session, upload a session-specific PDF and confirm the chat can
      use both its own document and general knowledge.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document admin general knowledge (Phase 2)"
```
