import hmac

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.routes.sessions import _format_messages
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import FRONTEND_DIR, get_settings
from app.models.schemas import (
    AdminRenameRequest,
    AdminResetPasswordRequest,
    AdminVerifyRequest,
    IngestResponse,
    MessagesResponse,
    SessionSummary,
)
from app.services import admin_users
from app.services.agent_service import AgentService, get_agent_service
from app.services.admin_users import UsernameTakenError
from app.services.general_kb import get_general_kb_id
from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
)
from app.services.session_service import SessionService, get_session_service

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


def require_admin_header(x_admin_password: str = Header(None)) -> None:
    _require_admin(x_admin_password or "")


@router.get("/admin/users")
def admin_list_users(
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
):
    return admin_users.list_users_with_counts(client)


@router.get("/admin/users/{user_id}/sessions", response_model=list[SessionSummary])
def admin_user_sessions(
    user_id: str,
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
):
    if client.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    # list_sessions is chatbot-scoped; a user id matches no chatbot's id, so
    # that call silently returns []. This admin listing must enumerate every
    # session the user owns across every chatbot, hence the owner-scoped call
    # (same fix already applied in admin_users.delete_user).
    return [
        {"id": r["id"], "name": r["name"], "updated_at": r.get("updated_at")}
        for r in client.list_sessions_by_owner(user_id)
    ]


@router.get("/admin/sessions/{session_id}/messages", response_model=MessagesResponse)
def admin_session_messages(
    session_id: str,
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
):
    row = client.get_session_row(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        raw = client.list_messages(session_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return MessagesResponse(messages=_format_messages(raw))


@router.post("/admin/users/{user_id}/reset-password", status_code=204)
def admin_reset_password(
    user_id: str,
    req: AdminResetPasswordRequest,
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
):
    if not admin_users.reset_password(client, user_id, req.password):
        raise HTTPException(status_code=404, detail="User not found")
    return Response(status_code=204)


@router.patch("/admin/users/{user_id}")
def admin_rename_user(
    user_id: str,
    req: AdminRenameRequest,
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
):
    if client.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        return admin_users.rename_user(client, user_id, req.username)
    except UsernameTakenError:
        raise HTTPException(status_code=409, detail="Username already taken")


@router.delete("/admin/users/{user_id}", status_code=204)
def admin_delete_user(
    user_id: str,
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
    agents: AgentService = Depends(get_agent_service),
):
    if not admin_users.delete_user(client, sessions, agents, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return Response(status_code=204)
