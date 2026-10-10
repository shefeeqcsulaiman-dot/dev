"""POST /api/v1/demo-data: fill the signed-in admin's company with sample data
(app/demo_data.py) as a background job; the browser polls GET /app-data/jobs/{id}."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import background
from app.auth_principal import Principal, get_current_principal
from app.database import get_db
from app.dependencies import require_company_admin

router = APIRouter(prefix="/demo-data", tags=["demo-data"], dependencies=[Depends(require_company_admin)])


@router.post("", status_code=202)
def fill_demo_data(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Company admins only. Safe to run again: records use stable DEMO- keys, so a second
    run updates them instead of adding copies."""
    from app.demo_data import run_for_token

    token = request.headers.get("Authorization", "")[7:]
    job = background.start(db, principal.company_id, "demo_data", token, lambda _db, _principal: run_for_token(token))
    return {"ok": True, **background.view(job), "job_id": job.id}
