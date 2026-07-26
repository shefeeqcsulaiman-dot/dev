from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db
from app.models import Company, User, uuid as _new_uuid
from app.schemas import CompanyOut, CompanyUpdate


router = APIRouter(prefix="/companies", tags=["companies"])

# Fields that must never be set to NULL (DB NOT NULL constraint)
_REQUIRED_FIELDS = {"name", "country"}


def _resolve_company(current_user: User, db: Session) -> "Company | None":
    """Return the user's company, auto-linking to an existing one if needed."""
    company = current_user.company
    if not company:
        # company_id mismatch — find the first available company and re-link
        company = db.query(Company).first()
        if company:
            current_user.company_id = company.id
            db.add(current_user)
            db.commit()
            db.refresh(current_user)
    return company


@router.get("/current", response_model=CompanyOut)
def current_company(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company = _resolve_company(current_user, db)
    if not company:
        raise HTTPException(status_code=404, detail="No company found")
    # This response can carry a large base64 logo, and is fetched on every page
    # load. ETag it on (id, updated_at) so a browser that already has the
    # current copy gets a tiny 304 instead of re-downloading it — the JSON
    # shape and data returned when it DOES change are completely unchanged.
    etag = f'"{company.id}-{company.updated_at.isoformat()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"},
        )
    return Response(
        content=CompanyOut.model_validate(company).model_dump_json(),
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"},
    )


@router.put("/current", response_model=CompanyOut)
def update_company(
    payload: CompanyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company = _resolve_company(current_user, db)
    if not company:
        # No existing company at all — create one
        company = Company(id=_new_uuid(), name=payload.name or "My Company")
        db.add(company)
        db.flush()
        current_user.company_id = company.id
        db.add(current_user)

    nullable_fields = [
        "trade_name", "emirate", "business_type", "business_activity",
        "legal_structure", "trade_license_no", "trade_license_issue_date",
        "trade_license_expiry", "free_zone", "address", "po_box",
        "phone", "website", "logo", "fta_username",
        "departments", "branches",
    ]
    for field in nullable_fields:
        val = getattr(payload, field, None)
        if val is not None:
            setattr(company, field, val or None)

    # Required fields: only update when a non-empty value is provided
    if payload.name:
        company.name = payload.name
    if payload.country:
        company.country = payload.country

    # TRN: unique nullable — only update when provided and non-empty
    if payload.trn:
        company.trn = payload.trn

    db.add(company)
    db.commit()
    db.refresh(company)
    return company
