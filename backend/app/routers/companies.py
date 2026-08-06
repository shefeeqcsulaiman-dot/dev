from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.dependencies import Principal, get_current_principal, get_current_user, get_db
from app.models import Company, User, uuid as _new_uuid
from app.schemas import CompanyOut, CompanyUpdate


router = APIRouter(prefix="/companies", tags=["companies"])

# Fields that must never be set to NULL (DB NOT NULL constraint)
_REQUIRED_FIELDS = {"name", "country"}


def _resolve_company(current_user: User) -> "Company | None":
    """Return the user's own company, or None if their company_id is orphaned.

    Previously fell back to `db.query(Company).first()` and silently re-linked
    the user to whichever company happened to sort first in the table — an
    arbitrary, unrelated tenant. That auto-grants an is_admin user full
    read/write access to a stranger's company data the moment any bug (or
    future migration) ever nulls/breaks a company_id. Fail closed instead:
    callers already handle a None company (404 on GET, create-a-new-company
    on PUT) rather than needing a guessed substitute.
    """
    return current_user.company


@router.get("/current", response_model=CompanyOut)
def current_company(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    # Widened to Employee/branch principals in Branch Management Phase 6 —
    # pos.html's checkAuth() needs this for currency/company-name display,
    # and read-only company info isn't sensitive enough to keep admin-only.
    if principal.is_admin:
        company = _resolve_company(principal.user)
    else:
        company = db.query(Company).filter(Company.id == principal.company_id).first()
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
    company = _resolve_company(current_user)
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
    if payload.currency:
        company.currency = payload.currency
    if payload.vat_rate is not None:
        company.vat_rate = payload.vat_rate

    # TRN: unique nullable — only update when provided and non-empty
    if payload.trn:
        company.trn = payload.trn

    db.add(company)
    db.commit()
    db.refresh(company)
    return company
