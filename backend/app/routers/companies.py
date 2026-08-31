import base64
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.dependencies import Principal, get_current_principal, get_current_user, get_db
from app.models import Company, User, uuid as _new_uuid
from app.schemas import CompanyOut, CompanyUpdate


router = APIRouter(prefix="/companies", tags=["companies"])

_DATA_URL_RE = re.compile(r"^data:([\w/+.-]+);base64,(.+)$", re.DOTALL)


def _to_company_out(company: Company) -> CompanyOut:
    """CompanyOut.model_validate() alone can't populate has_logo/logo_version
    -- they're derived from `logo`, not a same-named column -- so both GET
    and PUT /current build the response through here instead of leaving the
    PUT response with the schema's bare defaults (has_logo=False always)."""
    out = CompanyOut.model_validate(company)
    if company.logo:
        out.has_logo = True
        # Hash of the logo's own content only -- deliberately NOT
        # company.updated_at, which changes on every unrelated field edit.
        out.logo_version = hashlib.sha256(company.logo.encode()).hexdigest()[:12]
    return out


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
    # Fetched on every page load (index.html/hrms.html are separate
    # documents, not an SPA) — ETag on (id, updated_at) so a browser that
    # already has the current copy gets a tiny 304 instead of re-downloading
    # it. The logo itself no longer rides along in this payload at all (see
    # CompanyOut/_to_company_out()) — GET /companies/{id}/logo serves that
    # separately with its own content-based cache key.
    etag = f'"{company.id}-{company.updated_at.isoformat()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"},
        )
    return Response(
        content=_to_company_out(company).model_dump_json(),
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"},
    )


@router.get("/{company_id}/logo")
def company_logo(company_id: str, request: Request, db: Session = Depends(get_db)):
    """Serves a company's logo as a real, independently-cacheable image
    response — previously this same base64 blob was embedded directly
    inside GET /companies/current, which is fetched fresh on every single
    page load, and whose ETag was keyed on company.updated_at, so editing
    ANY company field (address, phone, anything) invalidated the cached
    logo too even though the logo itself hadn't changed, forcing a ~465KB
    re-download on the very next load regardless.

    Deliberately public/unauthenticated: a <img src="..."> tag can't attach
    an Authorization header, and a company's logo already appears on
    unauthenticated-facing documents (invoice/quotation PDFs a customer
    receives) — it isn't sensitive. company_id is a UUID, not guessable or
    enumerable, so this doesn't expose anything an attacker could target."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company or not company.logo:
        raise HTTPException(status_code=404, detail="No logo")
    m = _DATA_URL_RE.match(company.logo)
    if not m:
        raise HTTPException(status_code=404, detail="No logo")
    content_type, b64data = m.group(1), m.group(2)
    try:
        raw = base64.b64decode(b64data)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="No logo")
    # Content-based ETag (see _to_company_out()'s logo_version) — the
    # frontend also appends ?v=<logo_version> to this URL, so a re-uploaded
    # logo gets a brand-new URL immediately rather than waiting up to
    # max-age for a stale cached copy to expire.
    etag = f'"{hashlib.sha256(raw).hexdigest()[:16]}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=raw, media_type=content_type, headers=headers)


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
    # Every field above intentionally treats null-or-omitted as "leave
    # unchanged" (e.g. branch login's password field has the same "None =
    # don't touch" convention). logo is the one exception: removeLogo()
    # explicitly PUTs {logo: null} expecting it to actually clear the
    # field, which the loop above could never do -- model_fields_set is
    # what distinguishes "the client sent logo: null" from "the client
    # didn't mention logo at all", which a plain None can't.
    if "logo" in payload.model_fields_set and payload.logo is None:
        company.logo = None

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
    return _to_company_out(company)
