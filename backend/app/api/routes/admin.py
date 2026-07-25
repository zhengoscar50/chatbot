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
