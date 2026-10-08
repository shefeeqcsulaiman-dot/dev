import base64
import csv
import hashlib
import html
import io
import json
import os
import shutil
import subprocess
import tempfile
import re
import urllib.request
import zlib
import socket
import zipfile
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4
from xml.etree import ElementTree

import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import app.cache as cache
import app.timezone_utils as timezone_utils
from app import attendance_store
from app.accounting_posting import ensure_credit_note_tax_line, release_bank_matches
from app.config import get_settings
from app.database import get_db
from app.auth_principal import resolve_active_branch
from app.doc_index import parse_document_date, recompute_payment_targets
from app.department_scope import SCOPED_COLLECTIONS, assert_record_writable, build_index, filter_records, record_in_scope
from app.dependencies import Principal, branch_allows_module, company_allows_module, get_current_principal, get_current_user, principal_module_allowed, require_company_admin, require_module
from app.limiter import limiter
from app.module_integration import approve_and_post_source, sync_bill_accounting, sync_purchase_accounting, sync_receipt_payment_accounting, sync_sales_invoice_accounting, upsert_source_transaction
from app.routers.inventory import consume_valuation_layers
from app.rota_days import rota_in_range
from app.models import (
    PeriodLock,
    Job,
    Account,
    AppDataRecord,
    AttendanceDetail,
    AuditLog,
    AuditLogDetail,
    Branch,
    Company,
    Employee,
    GeneralLedgerEntry,
    InventoryValuationLayer,
    Invoice,
    InvoiceLine,
    JournalEntry,
    JournalLine,
    LeaveRequest,
    Payment,
    PostingJob,
    PayrollItem,
    PayrollRun,
    Receipt,
    SourceTransaction,
    SourceTransactionLine,
    StockMovement,
    StockProductMapping,
    TaxLine,
    User,
)

# Local time offset punches are recorded in — resolved per company via
# timezone_utils.company_utc_offset(), the same shared helper attendance.py
# and biotime_sync.py use, now that the offset is no longer a single
# hardcoded UAE constant.


router = APIRouter(prefix="/app-data", tags=["app data"])

# Collections whose writes should bust the report cache for the company.
_REPORT_AFFECTING_COLLECTIONS = frozenset({
    "salesInvoices", "bills", "payments", "expenses", "ledger",
    "journalDrafts", "purchaseDocuments", "purchaseRecords",
    "bankAccounts", "employees", "payrollRuns",
})

# Collections ess.py's _employee_app_data_records() short-TTL-caches (its own
# writes already invalidate directly, e.g. _create_request_record()/
# ess_update_task() -- this covers the OTHER write path, HR/admin editing the
# same collections through this generic bridge, e.g. approving a request or
# assigning a task from the HRMS side).
_ESS_CACHED_COLLECTIONS = frozenset({
    "tasks", "rotaAssignments", "overtimeRequests", "employeeLoans",
    "salaryAdvances", "attendanceCorrections", "hrHolidays",
})


def _invalidate_write_caches(collection: str, company_id: str) -> None:
    if collection in _REPORT_AFFECTING_COLLECTIONS:
        cache.invalidate_company(company_id)
    if collection in _ESS_CACHED_COLLECTIONS:
        cache.delete(f"ess_appdata:{company_id}:{collection}")

# Collections representing real financial transactions — subject to the same
# server-side period lock as manual journal vouchers (accounting.py). Without
# this, a closed/filed period could still be edited via a direct /app-data
# call even though the UI's period-lock toggle implies it can't be.
_PERIOD_LOCKED_COLLECTIONS: dict[str, str] = {
    "salesInvoices": "sales",
    "purchaseRecords": "purchase",
    "bills": "purchase",
    "expenses": "expense",
    "payrollRuns": "payroll",
}

# Collections branch-filtered for a branch-scoped Employee principal.
# Deliberately an explicit allowlist, not "every collection": each entry
# was added in its own phase, with its own dedicated test pass, even though
# AppDataRecord.branch_id has been stamped on every write since Phase 4 and
# filtering would be technically safe for any collection immediately.
_BRANCH_FILTERED_COLLECTIONS = frozenset({
    "purchaseRecords", "bills",  # Phase 5 — Inventory/Purchases
    "posSales", "salesInvoices",  # Phase 6 — POS/Sales
    "customers",  # POS customer picker — a branch only sees customers it
    # created (branch_id stamped on write); customers with no branch_id
    # (created by admin, or predating Branch Management) stay visible to
    # everyone via the branch_id IS NULL fallback below. Admin/User
    # principals have no branch_id at all, so this filter never applies to
    # them — they always see every customer, company-wide.

    # HR deep-audit — verified live (a second branch could read, overwrite,
    # and delete a first branch's loan/rota records) that none of these were
    # ever added despite the payroll deduction engine already being
    # branch-scoped at the calculation level. jobRequisitions/candidates are
    # deliberately NOT included here — recruiting data being company-wide
    # may be intentional, same precedent as the shared vendors/payments
    # collections, and wasn't verified either way.
    "employeeLoans", "salaryAdvances", "leaveRequests",
    "rotaShifts", "rotaSwaps", "rotaApprovals", "rotaDrafts", "rotaAssignments",
    "attendanceCorrections", "overtimeRequests",
})

# Superadmin's per-company Module Permissions, enforced against writes to the
# generic AppDataRecord store (this router's /app-data POST save/bulk-save
# actions are the single choke point almost every module's own data actually
# flows through — /app-data/records/{collection} GET below is the read-side
# counterpart). Built by tracing every literal collection name passed to
# saveServer()/saveRec() across app.js and pos.html — NOT exhaustive: a
# handful of collections (customers, vendors, payments, products, salesUnits,
# users, app_actions) are deliberately left unmapped because they're shared
# master/reference data read and written by more than one module (e.g.
# "payments" covers both sales receipts and purchase payments — see
# inferCollectionFromContext's payment-in/payment-out comment in app.js), so
# there's no single correct module to gate them under.
_COLLECTION_MODULE: dict[str, str] = {
    "salesInvoices": "sales", "salesCategories": "sales", "salesPeople": "sales",
    "quotations": "quotations", "quotationLayout": "quotations",
    "posSales": "pos", "serviceTypes": "pos",
    "bills": "purchase", "purchaseRecords": "purchase", "purchaseDocuments": "purchase",
    "stockMovements": "inventory",
    "expenses": "expense",
    "bankAccounts": "bank", "bankReconLines": "bank", "bankReconMatches": "bank", "bankReconSessions": "bank",
    "ledger": "accounting", "journalDrafts": "accounting", "recurringJournals": "accounting",
    "alertRules": "notifications",
    "employees": "hrms", "employeeLoans": "hrms", "salaryAdvances": "hrms",
    "leaveRequests": "hrms", "hrLeavePolicy": "hrms", "hr_settings": "hrms",
    "companyAnnouncements": "hrms",
    "attendanceCorrections": "hrms", "overtimeRequests": "hrms",
    "rotaShifts": "hrms", "rotaSwaps": "hrms", "rotaApprovals": "hrms", "rotaDrafts": "hrms", "rotaAssignments": "hrms",
    "jobRequisitions": "hrms", "candidates": "hrms",
    "payrollRuns": "hrms", "payrollAdjustments": "hrms",
}


def assert_collection_module_enabled(db: Session, principal: Principal, company: Company | None, collection: str) -> None:
    module = _COLLECTION_MODULE.get(collection)
    if not module:
        return
    company_modules = company.modules_enabled if company else None
    allowed = company_allows_module(company_modules, module)
    # completeSale() in pos.html writes posSales then mirrors it into
    # salesInvoices as an inherent part of completing a sale — that's core
    # POS behavior, not an optional cross-module feature, so a company/
    # branch with "pos" enabled but not "sales" separately toggled should
    # still be able to write this specific collection. Previously that
    # combination 403'd here, silently (pos.html swallows the error since
    # the sale itself already succeeded), so the sale would complete but
    # never appear in Sales & Invoices — see the "sale from pos not
    # showing" investigation.
    if not allowed and collection == "salesInvoices":
        allowed = company_allows_module(company_modules, "pos")
    if not allowed:
        raise HTTPException(status_code=403, detail=f"The '{module}' module is not enabled for your company")
    # Branch Login Phase 1: additional branch-level restriction, same
    # rationale as the require_module() composition in dependencies.py —
    # /app-data is the real write choke point for POS/Sales/Purchase/
    # Inventory (not the require_module-decorated routers), so it needs the
    # identical branch check to actually enforce the toggle on writes.
    if principal.branch_id:
        branch_modules = db.query(Branch.modules_enabled).filter(Branch.id == principal.branch_id).scalar()
        branch_ok = branch_allows_module(branch_modules, module)
        if not branch_ok and collection == "salesInvoices":
            branch_ok = branch_allows_module(branch_modules, "pos")
        if not branch_ok:
            raise HTTPException(status_code=403, detail=f"The '{module}' module is not enabled for your branch")


_SALARY_FIELDS = ("salary", "housing_allowance", "transport_allowance", "other_allowance")


def _redact_employee_salary(record: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """A role with employees:view (browse the Employee Directory) but not
    the field-level employees:view_salary add-on gets every "employees"
    collection record back with its salary figures nulled out, not just
    hidden client-side -- see docs comment on the permission catalog entry
    in hr_access.py. A User (is_admin) or a role with the permission is a
    no-op via principal.has()'s is_admin short-circuit."""
    if not principal.has("employees:view_salary"):
        for key in _SALARY_FIELDS:
            if key in record:
                record[key] = None
    return record


def normalize_document_date(value: Any) -> Any:
    """ISO YYYY-MM-DD when the date can be read; otherwise the original value, untouched."""
    parsed = parse_document_date(value)
    return parsed.isoformat() if parsed else value


def _record_period_date(record: dict[str, Any]) -> _dt.datetime | None:
    period = record.get("period")
    if period and re.match(r"^\d{4}-\d{2}", str(period)):
        try:
            return _dt.datetime.strptime(str(period)[:7], "%Y-%m").replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            pass
    for key in ("date", "invoice_date", "bill_date", "expense_date", "created_at"):
        # Non-ISO dates ("20-02-2023", common on AI-read invoices) used to fail to parse here,
        # so the period lock was silently skipped for them.
        parsed = parse_document_date(record.get(key))
        if parsed:
            return _dt.datetime(parsed.year, parsed.month, parsed.day, tzinfo=_dt.timezone.utc)
    return None


def assert_collection_period_open(db: Session, principal: Principal, collection: str, record: dict[str, Any]) -> None:
    module = _PERIOD_LOCKED_COLLECTIONS.get(collection)
    if not module:
        return
    from app.routers.accounting import assert_period_open
    assert_period_open(db, principal.company_id, module, _record_period_date(record))

# Never sent in bootstrap: their screens page them from the server.
_PAGED_COLLECTIONS = frozenset({
    "purchaseRecords", "salesInvoices", "bills", "payments", "quotations", "purchaseDocuments", "expenses",
    # Rota screens load the dates they show from /records/rotaAssignments/range.
    "rotaAssignments",
    # Legacy app-data ledger lines: the Ledger tab shows the real journal (GET /journal, paged).
    "ledger",
})

# Per-collection caps for bootstrap to prevent memory spikes on large accounts.
# Sized to cover ~1 year of data for a 50-employee UAE SME without truncation:
#   salesInvoices: not sent at all; the register pages them from GET /app-data/sales-invoices
#   leaveRequests/overtimeRequests: ~500-1 500/year → cap 500
#   payrollRuns: 12/year, cap 50 keeps 4 years of history
_BOOTSTRAP_COLLECTION_CAPS: dict[str, int] = {
    "products": 500,
    "salesCategories": 200,
    "salesUnits": 200,
    "journalDrafts": 300,
    "overtimeRequests": 500,
    "leaveRequests": 500,
    "attendanceCorrections": 500,
    "employeeLoans": 500,
    "salaryAdvances": 500,
    "jobRequisitions": 300,
    "candidates": 500,
    "payrollRuns": 50,
    "payrollAdjustments": 300,
    "audit": 50,
    # Same "uncapped, same shape of risk as rotaAssignments before its own
    # cap below" finding, just not yet measured on a live account. employees
    # is the one that matters most: each row also carries a base64 photo
    # (models.py's Employee.photo docstring), so it's both unbounded row
    # count AND a heavy per-row payload, same double risk profile
    # rotaAssignments had. rotaShifts/rotaSwaps/rotaApprovals/rotaDrafts/
    # tasks can all realistically grow the same way (bulk shift setup, an
    # actively-used Task Management module) with nothing else capping them.
    "employees": 500,
    "rotaShifts": 500,
    "rotaSwaps": 500,
    "rotaApprovals": 500,
    "rotaDrafts": 500,
    "tasks": 500,
}


def decimal_value(value: Any) -> Decimal:
    try:
        cleaned = re.sub(r"(?i)\b(AED|Dhs\.?|د\.إ)\b", "", str(value or "0")).strip()
        cleaned = cleaned.replace(" ", "")
        if "," in cleaned and "." not in cleaned:
            if re.search(r",[0-9]{1,2}$", cleaned):
                cleaned = cleaned.replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "")
        # Strip trailing non-numeric text (e.g. "5KG" → "5", "3Nos" → "3")
        m = re.match(r"^-?[0-9]*\.?[0-9]+", cleaned)
        if m:
            cleaned = m.group(0)
        return Decimal(cleaned or "0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def get_company_vat_rate(company: Any) -> Decimal:
    """Company-configured VAT rate, defaulting to 5% (UAE) when unset."""
    rate = getattr(company, "vat_rate", None) if company else None
    return rate if rate is not None else Decimal("5")


def principal_user_id(principal: Principal) -> str | None:
    """The acting User's id, for columns that are a strict ForeignKey to
    users.id (SourceTransaction.approved_by, PostingJob-related audit
    entries in accounting_posting.py) and would violate that constraint in
    production Postgres if fed an Employee's id instead. Deliberately
    returns None (a valid value for these nullable FKs) for an Employee
    principal rather than its id — those columns predate Employee logins
    ever reaching this write path (Branch Management Phase 4) and widening
    every one of them to accept either id is a separate, later change.
    AuditLog itself is the one exception: it got its own employee_id column
    (see log_action below) precisely so branch-login actions ARE attributed
    correctly in the one place that matters most for this phase."""
    return principal.user.id if principal.user else None


def resolve_principal_company(principal: Principal, db: Session) -> Company | None:
    """Same resolution bootstrap() already uses (see its is_admin branch) —
    the canonical way to get a full Company row for either principal kind,
    since only a User carries a `.company` relationship."""
    if principal.is_admin:
        from app.routers.companies import _resolve_company
        return _resolve_company(principal.user)
    return db.query(Company).filter(Company.id == principal.company_id).first()


def record_key(collection: str, record: dict[str, Any]) -> str | None:
    keys = {
        "products": "code",
        "customers": "name",
        "salesInvoices": "invoice_no",
        "quotations": "quote_no",
        "accounts": "code",
        "ledger": "ref",
        "bills": "bill_no",
        "vendors": "name",
        "payments": "ref",
        "bankAccounts": "iban",
        "expenses": "ref",
        "audit": "time",
        "invoiceLayout": "company",
        "inventorySettings": "key",
        "salesCategories": "name",
        "salesUnits": "code",
        "serviceTypes": "name",
        "purchaseRecords": "ref",
        "purchaseDocuments": "id",
        "journalDrafts": "ref",
        "corporateTax": "period",
        "relatedPartyTransactions": "party",
        "fixedAssets": "asset_code",
        "accrualsPrepayments": "reference",
        "costCenters": "code",
        "budgets": "cost_center",
        "cashFlowForecasts": "forecast_date",
        "creditControl": "customer_name",
        "consolidation": "subsidiary_name",
        "approvalMatrix": "module",
        "rotaShifts": "code",
        "rotaSwaps": "id",
        "rotaApprovals": "id",
        "rotaDrafts": "id",
        "rotaAssignments": "id",
        "app_actions": "id",
        "invoice-layouts-pack": "id",
    }
    field = keys.get(collection)
    if field and record.get(field):
        return str(record[field])
    for field in ("id", "reference", "name", "code", "ref"):
        if record.get(field):
            return str(record[field])
    return None


def _backfill_employee_branch_ids(db: Session, company_id: str) -> None:
    """One-time, idempotent: attach a real branch_id to "employees" rows
    that predate the branch-name lookup app.js's employee form has done at
    save time since Branch Management Phase 1 (frontend/public/taxflow/
    src/app.js ~line 711) — those legacy rows only ever got the free-text
    branch/location name. Runs lazily on list; rows that already have a
    branch_id are skipped, so repeat calls are cheap no-ops."""
    branches = db.query(Branch.id, Branch.name).filter(Branch.company_id == company_id).all()
    if not branches:
        return
    by_name = {name.strip().lower(): bid for bid, name in branches if name}
    rows = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "employees",
            AppDataRecord.branch_id.is_(None),
        )
        .all()
    )
    if not rows:
        return
    changed = False
    for row in rows:
        try:
            payload = json.loads(row.payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        name = str(payload.get("branch") or payload.get("location") or "").strip().lower()
        matched = by_name.get(name)
        if matched:
            row.branch_id = matched
            changed = True
    if changed:
        db.commit()


def serialize(record: AppDataRecord) -> dict[str, Any]:
    try:
        data = json.loads(record.payload)
    except json.JSONDecodeError:
        return {}
    # AppDataRecord.branch_id (the authoritative attribution, stamped at
    # write time from the payload or the writer's principal — see
    # app_data_action()) isn't always mirrored into the JSON payload itself,
    # so callers reading the payload alone (branch badges in list views,
    # Phase 7) would see nothing for records created without an explicit
    # branch_id in the body. Surface the row's value as a fallback, without
    # clobbering an explicit payload value.
    if isinstance(data, dict) and record.branch_id and not data.get("branch_id"):
        data["branch_id"] = record.branch_id
    return data


def _collection_read_filters(principal: Principal, collection: str, branch_id: str | None) -> list[Any]:
    """Company + collection + branch scoping shared by every app-data list read."""
    base_filters = [
        AppDataRecord.company_id == principal.company_id,
        AppDataRecord.collection == collection,
    ]
    collection_module = _COLLECTION_MODULE.get(collection)
    cross_branch = bool(collection_module and principal.can_cross_branch(collection_module))
    if principal.branch_id and collection in _BRANCH_FILTERED_COLLECTIONS and not cross_branch:
        # Branch-scoped Employee — locked to their own accessible branch(es)
        # (Phase 3: possibly more than one, via EmployeeBranchAccess),
        # regardless of any ?branch_id= passed in beyond that set (an
        # explicit param here could otherwise be used to peek at another
        # branch's records) — unless their role grants
        # "<module>:view_all_branches" (Branch Security Layer Phase 2).
        active_branch = resolve_active_branch(principal, branch_id)
        base_filters.append(
            (AppDataRecord.branch_id == active_branch) | (AppDataRecord.branch_id.is_(None))
        )
    elif branch_id:
        # Company-wide viewer (admin) explicitly asking to see one branch —
        # Phase 7 branch switcher. Opt-in, so it applies to any collection,
        # not just the _BRANCH_FILTERED_COLLECTIONS allowlist above.
        base_filters.append(AppDataRecord.branch_id == branch_id)
    return base_filters


# Employee photos are base64 data URLs inside each "employees" record, often tens of
# KB each, and every HRMS/main page load used to carry all of them. Lists now send a
# URL instead (EMPLOYEE_PHOTO_PATH), served as a cacheable image by employee_photo().
# The URL includes a hash of the photo, so it is unguessable and changes when the
# photo does; saving a record that still carries the URL keeps the stored photo.
EMPLOYEE_PHOTO_PATH = "/api/v1/app-data/employee-photo/"


def _photo_version(photo: str) -> str:
    return hashlib.sha256(photo.encode()).hexdigest()[:16]


def serialize_for_list(row: AppDataRecord) -> dict[str, Any]:
    """serialize(), with an employee photo replaced by its image URL."""
    data = serialize(row)
    photo = data.get("photo") if isinstance(data, dict) and row.collection == "employees" else None
    if isinstance(photo, str) and photo.startswith("data:"):
        data["photo"] = f"{EMPLOYEE_PHOTO_PATH}{row.id}?v={_photo_version(photo)}"
    return data


def _keep_stored_employee_photo(record: dict[str, Any], existing: AppDataRecord | None) -> None:
    """An edit form that re-reads its photo preview sends the photo URL back; keep the
    stored photo instead of saving the URL over it."""
    photo = record.get("photo")
    if not (isinstance(photo, str) and EMPLOYEE_PHOTO_PATH in photo):
        return
    stored = _stored_payload(existing) if existing else None
    stored_photo = stored.get("photo") if isinstance(stored, dict) else None
    if isinstance(stored_photo, str) and stored_photo.startswith("data:"):
        record["photo"] = stored_photo
    else:
        record.pop("photo", None)


def _start_job(request: Request, db: Session, principal: Principal, kind: str, work) -> dict[str, object]:
    """Run `work(db, principal)` as a background job (app/background.py) and return its id;
    the browser polls GET /app-data/jobs/{id} for the result."""
    from app import background

    token = request.headers.get("Authorization", "")[7:]
    job = background.start(db, principal.company_id, kind, token, work)
    return {"ok": True, **background.view(job), "job_id": job.id}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)) -> dict[str, object]:
    """Status of a background job started by this company; `result` once completed,
    `error` once failed."""
    from app import background

    job = db.query(Job).filter(Job.id == job_id, Job.company_id == principal.company_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return background.view(job)


@router.get("/employee-photo/{record_id}")
def employee_photo(record_id: str, request: Request, v: str = Query(default="", max_length=32), db: Session = Depends(get_db)):
    """An employee photo as an image. No login header: an <img src> can't send one.
    The URL is a capability instead: the record id is a random UUID and v must match a
    hash of the photo itself, and both only reach someone who could already read the
    employee list. Immutable caching: a new photo gets a new v."""
    row = db.query(AppDataRecord).filter(AppDataRecord.id == record_id, AppDataRecord.collection == "employees").first()
    stored = _stored_payload(row) if row else None
    photo = stored.get("photo") if isinstance(stored, dict) else None
    if not (isinstance(photo, str) and photo.startswith("data:") and v and v == _photo_version(photo)):
        raise HTTPException(status_code=404, detail="No photo")
    match = re.match(r"^data:([\w/+.-]+);base64,(.*)$", photo, re.S)
    if not match:
        raise HTTPException(status_code=404, detail="No photo")
    try:
        raw = base64.b64decode(match.group(2))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="No photo")
    etag = f'"{v}"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=31536000, immutable"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=raw, media_type=match.group(1), headers=headers)


@router.get("/records/{collection}")
def list_collection_records(
    collection: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    # Widened from admin-only in Branch Management Phase 4 — an Employee/
    # branch login needs to read reference data (products, customers, etc.)
    # to write POS sales/purchases at all. Branch-filtering of results is
    # now applied for collections in _BRANCH_FILTERED_COLLECTIONS (Phase 5:
    # purchaseRecords) — other collections still show every record in the
    # company to every principal, same as before this endpoint existed for
    # Employees at all (see _BRANCH_FILTERED_COLLECTIONS's own comment for
    # why this is an explicit allowlist, not blanket filtering).
    company = resolve_principal_company(principal, db)
    assert_collection_module_enabled(db, principal, company, collection)
    assert_collection_read_permission(principal, collection)
    if collection == "employees":
        # This generic endpoint previously had no RBAC check at all for the
        # "employees" collection -- only the module-enabled check above --
        # so an Employee principal could read the full roster (salary
        # included) by calling this directly, bypassing the RBAC filtering
        # bootstrap()'s _allowed_bootstrap_collections() already applies.
        # User/Branch principals are unaffected: is_admin bypasses .has(),
        # and Branch principals can never reach here at all ("hrms" is
        # outside BRANCH_ELIGIBLE_MODULES, so assert_collection_module_enabled
        # already 403s them above).
        if not principal.has("employees:view"):
            raise HTTPException(status_code=403, detail="Not permitted")
        _backfill_employee_branch_ids(db, principal.company_id)
    base_filters = _collection_read_filters(principal, collection, branch_id)
    if principal.is_dept_scoped and collection in SCOPED_COLLECTIONS:
        # A department-scoped login can only be paginated AFTER filtering, so
        # fetch the whole collection (per-company sizes are small) and slice.
        all_rows = (
            db.query(AppDataRecord)
            .filter(*base_filters)
            .order_by(AppDataRecord.created_at.desc(), AppDataRecord.id.desc())
            .all()
        )
        visible = filter_records(db, principal, collection, [serialize_for_list(row) for row in all_rows])
        total = len(visible)
        records = visible[offset:offset + limit]
        if collection == "employees":
            records = [_redact_employee_salary(r, principal) for r in records]
        return {
            "ok": True, "collection": collection, "records": records, "limit": limit, "offset": offset,
            "total": total, "has_more": offset + len(records) < total,
        }
    total = db.query(func.count(AppDataRecord.id)).filter(*base_filters).scalar() or 0
    rows = (
        db.query(AppDataRecord)
        .filter(*base_filters)
        .order_by(AppDataRecord.created_at.desc(), AppDataRecord.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    records = [serialize_for_list(row) for row in rows]
    if collection == "employees":
        records = [_redact_employee_salary(r, principal) for r in records]
    return {
        "ok": True,
        "collection": collection,
        "records": records,
        "limit": limit,
        "offset": offset,
        "total": total,
        "has_more": offset + len(rows) < total,
    }


# Reference lists the pickers search (product, customer, supplier fields on invoices,
# quotations, purchases). Bootstrap only carries the newest of them (products: 500), so
# a picker that only filtered what was loaded couldn't find the rest.
_CATALOG_COLLECTIONS = frozenset({"products", "customers", "vendors"})


@router.get("/catalog/{collection}")
def search_catalog(
    collection: str,
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Records of products/customers/vendors whose key (code or name) or saved fields
    contain `q` (any case), keys starting with `q` first. Same access rules as
    GET /records/{collection}."""
    if collection not in _CATALOG_COLLECTIONS:
        raise HTTPException(status_code=404, detail="Not a searchable list")
    company = resolve_principal_company(principal, db)
    assert_collection_module_enabled(db, principal, company, collection)
    assert_collection_read_permission(principal, collection)
    filters = _collection_read_filters(principal, collection, None)
    needle = q.strip().lower()
    if needle:
        like = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        # The payload is JSON text, so names and other fields are matched inside it;
        # \u-escaped characters (json.dumps default) are matched through the key only.
        filters.append(or_(
            func.lower(AppDataRecord.record_key).like(like, escape="\\"),
            func.lower(AppDataRecord.payload).like(like, escape="\\"),
        ))
        prefix = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rank = case((func.lower(AppDataRecord.record_key).like(prefix, escape="\\"), 0), else_=1)
        order = (rank, func.lower(AppDataRecord.record_key), AppDataRecord.id)
    else:
        order = (AppDataRecord.created_at.desc(), AppDataRecord.id.desc())
    rows = db.query(AppDataRecord).filter(*filters).order_by(*order).limit(limit).all()
    return {"ok": True, "collection": collection, "records": [serialize_for_list(row) for row in rows]}


def _rota_read_filters(db: Session, principal: Principal, branch_id: str | None) -> list[Any]:
    """Company + branch scoping for rotaAssignments reads (department scoping is applied
    to the parsed records afterwards with filter_records())."""
    company = resolve_principal_company(principal, db)
    assert_collection_module_enabled(db, principal, company, "rotaAssignments")
    base_filters = [AppDataRecord.company_id == principal.company_id, AppDataRecord.collection == "rotaAssignments"]
    collection_module = _COLLECTION_MODULE.get("rotaAssignments")
    cross_branch = bool(collection_module and principal.can_cross_branch(collection_module))
    if principal.branch_id and "rotaAssignments" in _BRANCH_FILTERED_COLLECTIONS and not cross_branch:
        active_branch = resolve_active_branch(principal, branch_id)
        base_filters.append(
            (AppDataRecord.branch_id == active_branch) | (AppDataRecord.branch_id.is_(None))
        )
    elif branch_id:
        base_filters.append(AppDataRecord.branch_id == branch_id)
    return base_filters


@router.get("/records/rotaAssignments/range")
def list_rota_assignments_in_range(
    date_from: str = Query(alias="from"),
    date_to: str = Query(alias="to"),
    employee_id: str | None = Query(default=None, max_length=80),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Every rotaAssignments row whose date falls in [from, to], regardless of the
    _BOOTSTRAP_COLLECTION_CAPS["rotaAssignments"] = 500 cap bootstrap()/list_collection_records
    apply (newest-created-first, so an older or future week's shifts can silently fall
    outside it once a company has more than 500 saved). Monthly/Weekly/Department Rota
    call this for whatever range they're actually displaying instead of relying on
    the capped bootstrap blob alone -- same department/branch scoping as every other
    read of this collection, just no row limit on the date window itself (a rota grid
    is bounded by staff count x 7, not by how long the company has been using rotas).
    Rota assignments are not in the bootstrap at all any more, so this is how every rota
    screen gets them. employee_id: only that employee's shifts (e.g. upcoming shifts for
    a swap request)."""
    if date_from > date_to:
        raise HTTPException(status_code=400, detail="'from' must not be after 'to'")
    base_filters = [*_rota_read_filters(db, principal, branch_id), *rota_in_range(date_from, date_to)]
    if employee_id:
        base_filters.append(AppDataRecord.payload.contains(json.dumps(employee_id)))
    rows = db.query(AppDataRecord).filter(*base_filters).order_by(AppDataRecord.created_at, AppDataRecord.id).all()
    records = [
        r for r in (serialize(row) for row in rows)
        if date_from <= str(r.get("date") or "") <= date_to and (not employee_id or r.get("employee_id") == employee_id)
    ]
    records = filter_records(db, principal, "rotaAssignments", records)
    return {"ok": True, "collection": "rotaAssignments", "from": date_from, "to": date_to, "records": records}


@router.get("/records/rotaAssignments/by-ids")
def list_rota_assignments_by_ids(
    ids: str = Query(min_length=1, max_length=8000),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Specific rotaAssignments rows by id (comma-separated, up to 200), e.g. the two
    shifts a swap request refers to, whatever week they fall in."""
    wanted = [i.strip() for i in ids.split(",") if i.strip()][:200]
    rows = (
        db.query(AppDataRecord)
        .filter(*_rota_read_filters(db, principal, branch_id), AppDataRecord.record_key.in_(wanted))
        .all()
    ) if wanted else []
    records = filter_records(db, principal, "rotaAssignments", [serialize(row) for row in rows])
    return {"ok": True, "collection": "rotaAssignments", "records": records}


# Maps each sidebar module (HR and, since the "Main Dashboard Access" phase,
# main-app modules too) to the app-data collection(s) it reads. Used to scope
# the bootstrap blob for an Employee principal to only what their role can
# view — a collection not listed under any module the Employee's role grants
# `:view` on is never returned, full stop.
_COLLECTIONS_BY_MODULE: dict[str, list[str]] = {
    "employees": ["employees", "staff"],  # "staff" is a legacy alias sync_domain_model() still accepts
    "leave": ["leaveRequests"],
    "attendance": ["attendanceCorrections"],
    "rota": ["rotaShifts", "rotaSwaps", "rotaApprovals", "rotaDrafts", "rotaAssignments"],
    "overtime": ["overtimeRequests"],
    "loans": ["employeeLoans", "salaryAdvances"],
    "recruitment": ["jobRequisitions", "candidates"],
    "payroll": ["payrollRuns", "payrollAdjustments"],
    # Task Management (sidebar data-module="hr_workflow"). Was missing, so a
    # role granted hr_workflow:view never received tasks from bootstrap.
    "hr_workflow": ["tasks"],
    # Main-dashboard modules — products/customers/vendors are shared
    # reference data needed by more than one module, so they're duplicated
    # across every module list that plausibly needs them; the union below
    # dedupes naturally.
    "sales": ["salesInvoices", "salesCategories", "salesUnits", "serviceTypes", "salesPeople", "customers", "products"],
    "quotations": ["quotations", "quotationLayout"],
    "purchase": ["bills", "purchaseDocuments", "vendors", "products"],
    "inventory": ["products", "inventorySettings"],
    "expense": ["expenses"],
    "bank": ["bankAccounts", "payments"],
    "accounting": ["ledger", "journalDrafts", "accounts", "recurringJournals", "lockedPeriods"],
    "corporate": [
        "corporateTax", "fixedAssets", "accrualsPrepayments", "costCenters",
        "budgets", "cashFlowForecasts", "creditControl", "consolidation", "approvalMatrix",
        "relatedPartyTransactions",
    ],
    "notifications": ["alertRules"],
}


def _allowed_bootstrap_collections(principal: Principal) -> set[str] | None:
    """None means unrestricted (admin User). For an Employee principal,
    returns exactly the collections their role's module `:view` permissions
    unlock — never the full set, regardless of how many permissions they
    have."""
    if principal.is_admin:
        return None
    allowed: set[str] = set()
    for module, collections in _COLLECTIONS_BY_MODULE.items():
        if principal.has(f"{module}:view"):
            allowed.update(collections)
    return allowed


# Only the HR-sensitive modules get a write-permission gate -- NOT
# sales/purchase/inventory/bank/accounting/corporate/notifications, where
# "module enabled for the company/branch" is a deliberate, already-tested
# design (Branch Management Phase 4: test_branch_write_access.py,
# test_branch_role_module_inheritance.py) letting any Employee/Branch login
# write those collections without a separate per-collection view permission.
# Narrowing to this list -- rather than mirroring _allowed_bootstrap_collections
# for every module -- is what keeps that intentional behavior intact while
# still closing the real gap: an Employee whose role grants none of these HR
# permissions could otherwise write straight into payroll/loans/leave/rota/
# employees data (e.g. another employee's own bank IBAN -- see sync_domain_model's
# "employees"/"staff" handling, which writes straight into the payload the WPS
# payroll export later reads verbatim). "employees" is safe to include here:
# ESS's own self-edit-contact-details feature (PUT /ess/profile-details) never
# goes through this endpoint at all -- it edits the same AppDataRecord directly,
# through its own allow-listed field set (mobile/address/emergency contact
# only, explicitly never salary/IBAN/documents) with its own audit trail.
_HR_WRITE_PERMISSION_MODULES = frozenset({
    "employees", "leave", "attendance", "rota", "overtime", "loans", "recruitment", "payroll", "hr_workflow",
})


# "employees" deliberately stays on the `:view`-gated check below, not the
# `:edit` one -- unlike the other HR modules, its protection is field-level:
# a `:view`-only role IS allowed to write ordinary profile fields (name,
# department, designation...), with basic_salary/allowances silently
# redacted server-side for anyone lacking employees:view_salary (see
# _redact_employee_salary() and test_employees_salary_permission.py's
# test_write_side_ignores_salary_fields_without_permission). Requiring
# `:edit` here would block that already-correct, narrower redaction model
# entirely rather than tightening it.
_HR_EDIT_GATED_MODULES = _HR_WRITE_PERMISSION_MODULES - {"employees"}

_HR_MODULE_LABELS = {
    "employees": "Employees", "leave": "Leave", "attendance": "Attendance", "rota": "Rota & Shift",
    "overtime": "Overtime", "loans": "Loans & Advances", "recruitment": "Recruitment", "payroll": "Payroll",
    "hr_workflow": "Task Management",
}
_ADMIN_ONLY_COLLECTIONS = frozenset({"users", "invoiceLayout"})
_HR_SETTINGS_COLLECTIONS = frozenset({"companyAnnouncements"})
_HR_COLLECTION_MODULE = {
    c: m for m, cs in _COLLECTIONS_BY_MODULE.items() if m in _HR_WRITE_PERMISSION_MODULES for c in cs
}
_EMPLOYEE_SENSITIVE_FIELDS = (
    "salary", "basic_salary", "housing_allowance", "transport_allowance", "other_allowance",
    "iban", "wps_id", "status", "branch_id",
)


# Main-app collections and the module whose role permission they need (HR collections have their
# own gates below). Kept apart from _COLLECTION_MODULE, which drives the company-level module
# switch: products/customers/vendors/accounts must stay writable whatever modules a company has.
_ROLE_GATED_COLLECTIONS: dict[str, str] = {
    **{c: m for c, m in _COLLECTION_MODULE.items() if m != "hrms"},
    "products": "inventory", "customers": "sales", "vendors": "purchase", "accounts": "accounting",
}
_COLLECTION_ROLE_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "products": ("sales", "pos", "purchase"),
    "customers": ("pos", "quotations"),
    "vendors": ("expense", "bank"),
}


def _assert_main_module_role(principal: Principal, collection: str, write: bool, delete: bool = False) -> None:
    """A login may only read/write a main-app collection its role covers (see
    principal_module_allowed). Collections with no module (layouts, settings) can't be written
    by a read-only main-app role."""
    if principal.is_admin:
        return
    module = _ROLE_GATED_COLLECTIONS.get(collection)
    if module is None:
        if principal.kind == "user" and write and not any(k.endswith(":edit") for k in principal.permissions):
            raise HTTPException(status_code=403, detail="Your role is read-only")
        return
    options = (module, *_COLLECTION_ROLE_ALTERNATIVES.get(collection, ()))
    if not any(principal_module_allowed(principal, m, write, delete) for m in options):
        raise HTTPException(
            status_code=403,
            detail="Your role can't delete here" if delete else "Your role can't make changes here" if write else "Your role doesn't have access to this",
        )


def assert_collection_read_permission(principal: Principal, collection: str) -> None:
    """Employee logins with no role, or a role lacking the HR module's
    `:view`, must not list HR collections (other people's loans/advances...)."""
    if principal.is_admin:
        return
    if principal.kind == "employee" and not principal.permissions:
        raise HTTPException(status_code=403, detail="Not permitted")
    _assert_main_module_role(principal, collection, write=False)
    module = _HR_COLLECTION_MODULE.get(collection)
    if module and not principal.has(f"{module}:view"):
        raise HTTPException(status_code=403, detail="Not permitted")
    if collection in _ADMIN_ONLY_COLLECTIONS:
        raise HTTPException(status_code=403, detail="Not permitted")


def _guard_employee_write(db: Session, principal: Principal, collection: str, record: dict[str, Any]) -> dict[str, Any]:
    """Without employees:edit, salary/IBAN/status/branch always keep their
    stored values and new employees can't be created."""
    if collection not in ("employees", "staff") or principal.has("employees:edit"):
        return record
    key = record_key(collection, record)
    existing = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == principal.company_id,
            AppDataRecord.collection == collection,
            AppDataRecord.record_key == key,
        )
        .first()
        if key else None
    )
    if not existing:
        raise HTTPException(status_code=403, detail="You don't have permission to create employees")
    try:
        stored = json.loads(existing.payload or "{}")
    except (TypeError, json.JSONDecodeError):
        stored = {}
    out = dict(record)
    for field_name in _EMPLOYEE_SENSITIVE_FIELDS:
        if field_name in stored:
            out[field_name] = stored[field_name]
        else:
            out.pop(field_name, None)
    return out


def assert_collection_write_permission(principal: Principal, collection: str) -> None:
    """Gates writes to HR-sensitive collections (_HR_WRITE_PERMISSION_MODULES)
    on the role's `:edit` permission for that module -- NOT `:view` -- for
    every module except "employees" (see _HR_EDIT_GATED_MODULES above).
    Every other module has its own `:edit` permission in the RBAC catalog
    (hr_access.py's _PERMISSION_CATALOG) specifically so a role can be
    granted read-only access; this used to reuse
    _allowed_bootstrap_collections() (the `:view`-only read-side gate) for
    writes too, so a role granted only e.g. "Loans — View" could still
    approve/edit/delete any loan or overtime record company-wide via a
    direct API call, despite appearing read-only in the permission UI."""
    if principal.is_admin:
        return
    if principal.kind == "employee" and not principal.permissions:
        raise HTTPException(status_code=403, detail="You don't have permission to modify this data")
    _assert_main_module_role(principal, collection, write=True)
    if collection in _ADMIN_ONLY_COLLECTIONS:
        raise HTTPException(status_code=403, detail="You don't have permission to modify this data")
    if collection in _HR_SETTINGS_COLLECTIONS and not principal.has("hr_settings:edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to modify this data")
    gated_collections = {
        c for module, collections in _COLLECTIONS_BY_MODULE.items()
        if module in _HR_WRITE_PERMISSION_MODULES for c in collections
    }
    if collection not in gated_collections:
        return
    allowed: set[str] = set()
    for module, collections in _COLLECTIONS_BY_MODULE.items():
        if module not in _HR_WRITE_PERMISSION_MODULES:
            continue
        needed = f"{module}:edit" if module in _HR_EDIT_GATED_MODULES else f"{module}:view"
        if principal.has(needed):
            allowed.update(collections)
    if collection not in allowed:
        label = _HR_MODULE_LABELS.get(_HR_COLLECTION_MODULE.get(collection, ""), "this section")
        raise HTTPException(
            status_code=403,
            detail=f"Your role can't make changes in {label}. Ask an administrator to give your role {label} — Edit.",
        )


# Modules whose catalog has a separate "delete" permission (attendance/payroll don't).
_HR_DELETE_GATED_MODULES = frozenset({"employees", "leave", "rota", "overtime", "loans", "recruitment", "hr_workflow"})


def assert_collection_delete_permission(principal: Principal, collection: str) -> None:
    """Write gate plus the module's own `:delete` -- previously an `:edit` role could delete too."""
    assert_collection_write_permission(principal, collection)
    if principal.is_admin:
        return
    _assert_main_module_role(principal, collection, write=True, delete=True)
    module = _HR_COLLECTION_MODULE.get(collection)
    if module in _HR_DELETE_GATED_MODULES and not principal.has(f"{module}:delete"):
        label = _HR_MODULE_LABELS.get(module, "this section")
        raise HTTPException(
            status_code=403,
            detail=f"Your role can't delete in {label}. Ask an administrator to give your role {label} — Delete.",
        )


# hrms.html (window.HRMS_STANDALONE) never renders anything from the
# Sales/Purchase/Accounting/Corporate modules, but before this existed it
# still paid for all of it: an admin User's unrestricted bootstrap (the
# `is_admin -> None` case above) sent, and hydrateFromServer() then parsed
# and rendered, the company's full product/invoice/quotation/ledger/etc.
# history on every HRMS page load. `?scope=hrms` (passed by login.html's
# HRMS-redirect preload and by hydrateFromServer() when HRMS_STANDALONE)
# restricts the response to just the modules the HRMS portal actually
# reads, intersected with (never widening) whatever an Employee principal's
# own permissions already allow.
_HRMS_SCOPE_MODULES = [
    "employees", "leave", "attendance", "rota", "overtime", "loans",
    "recruitment", "payroll", "hr_workflow", "bank", "notifications",
]


def _hrms_scope_collections() -> set[str]:
    return {c for m in _HRMS_SCOPE_MODULES for c in _COLLECTIONS_BY_MODULE.get(m, [])}


# `?scope=main` (hydrateFromServer() when NOT window.HRMS_STANDALONE, i.e.
# index.html) is the mirror-image fix of scope=hrms above: index.html has no
# DOM elements at all for Leave Management, Rota, Overtime, Loans &
# Advances, Recruitment, or Task Management (grepped confirmed -- it does
# still use employees/payroll/bank/notifications, unlike hrms.html, which is
# why those stay OUT of this exclusion list, the opposite of _HRMS_SCOPE_MODULES
# above). An EXCLUSION list rather than building an inclusion set the way
# scope=hrms does deliberately, since _COLLECTIONS_BY_MODULE isn't a complete
# catalog of every collection that can exist (e.g. invoiceLayout/
# quotationLayout aren't module-mapped at all) -- inverting it into an
# inclusion set would silently drop any collection not listed here, which
# scope=hrms accepts as a tradeoff (HRMS genuinely only needs a known,
# enumerable set) but scope=main should not.
_MAIN_SCOPE_EXCLUDED_MODULES = [
    "leave", "attendance", "rota", "overtime", "loans", "recruitment", "hr_workflow",
]


def _main_scope_excluded_collections() -> set[str]:
    return {c for m in _MAIN_SCOPE_EXCLUDED_MODULES for c in _COLLECTIONS_BY_MODULE.get(m, [])}


def _parse_modules_enabled(raw: str | None) -> list[str] | None:
    """Superadmin's per-company Module Permissions (companies.modules_enabled,
    a JSON array) — None means "not restricted, show everything" (matches
    superadmin.py's own ALL_MODULES fallback for a null/blank column, so a
    company created before this column existed isn't suddenly locked down)."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else None
    except (TypeError, ValueError):
        return None


@router.get("")
@limiter.limit("60/minute")
def bootstrap(
    request: Request,
    scope: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    # Cache admin bootstrap responses briefly — this is the single most
    # expensive, most frequently-called read in the app (every page
    # navigation re-fetches it) and previously had no caching at all, unlike
    # dashboard/summary/trial-balance. Deliberately admin-only and a short
    # TTL: an Employee principal's response depends on their role's specific
    # permissions (_allowed_bootstrap_collections), so caching it under a
    # company-wide key could leak one role's filtered view to a different
    # role, or serve a stale filtered view after a permission change — not
    # worth the risk for a lower-traffic path. 8s is short enough that
    # staleness is barely noticeable but still absorbs bursts of repeated
    # calls (e.g. clicking through several pages in quick succession).
    # `scope` is folded into the cache key so an HRMS-scoped response can
    # never be served back to a full-dashboard request or vice versa.
    cache_key = f"bootstrap:{principal.company_id}:{scope}" if scope else f"bootstrap:{principal.company_id}"
    if principal.is_admin:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    _backfill_employee_branch_ids(db, principal.company_id)
    cap = get_settings().bootstrap_record_cap
    allowed_collections = _allowed_bootstrap_collections(principal)
    if scope == "hrms":
        hrms_collections = _hrms_scope_collections()
        allowed_collections = hrms_collections if allowed_collections is None else (allowed_collections & hrms_collections)
    # See _main_scope_excluded_collections()'s own comment for why this is an
    # exclusion set threaded alongside allowed_collections, rather than
    # folded into it the way scope=hrms folds its inclusion set above.
    excluded_collections = _main_scope_excluded_collections() if scope == "main" else set()
    # Collections with large record counts are fetched with DB-level LIMIT to avoid
    # loading and deserializing thousands of rows that will be discarded in Python.
    _HEAVY_COLLECTIONS = {c for c, n in _BOOTSTRAP_COLLECTION_CAPS.items() if n <= 500}
    heavy_results: dict[str, list[dict[str, Any]]] = {}
    # One GROUP BY instead of a separate COUNT(*) per heavy collection (was
    # 17 round trips — costs more on production Postgres than locally on
    # SQLite, where there's no real network latency per query).
    _heavy_coll_names = [
        c for c in _HEAVY_COLLECTIONS
        if (allowed_collections is None or c in allowed_collections) and c not in excluded_collections
    ]
    heavy_totals: dict[str, int] = dict(
        db.query(AppDataRecord.collection, func.count(AppDataRecord.id))
        .filter(
            AppDataRecord.company_id == principal.company_id,
            AppDataRecord.collection.in_(_heavy_coll_names),
        )
        .group_by(AppDataRecord.collection)
        .all()
    )
    for coll, coll_cap in _BOOTSTRAP_COLLECTION_CAPS.items():
        if coll_cap > 500:
            continue  # low-cap collections handled in the bulk query below
        if allowed_collections is not None and coll not in allowed_collections:
            continue
        if coll in excluded_collections:
            continue
        rows = (
            db.query(AppDataRecord)
            .filter(AppDataRecord.company_id == principal.company_id, AppDataRecord.collection == coll)
            .order_by(AppDataRecord.created_at.desc())
            .limit(coll_cap)
            .all()
        )
        rows.reverse()
        heavy_results[coll] = [serialize_for_list(r) for r in rows]

    # Bulk query for all remaining (non-heavy) collections
    bulk_query = db.query(AppDataRecord).filter(
        AppDataRecord.company_id == principal.company_id,
        # These page from the server instead: purchaseRecords via /records/purchaseRecords,
        # the rest via /sales-invoices and /registers/<collection> (routers/registers.py).
        AppDataRecord.collection.notin_(_HEAVY_COLLECTIONS | _PAGED_COLLECTIONS),
    )
    if allowed_collections is not None:
        bulk_query = bulk_query.filter(AppDataRecord.collection.in_(allowed_collections))
    if excluded_collections:
        bulk_query = bulk_query.filter(AppDataRecord.collection.notin_(excluded_collections))
    records = bulk_query.order_by(AppDataRecord.created_at.desc()).limit(cap).all()
    records.reverse()

    grouped: dict[str, list[dict[str, Any]]] = {**heavy_results}
    collection_totals: dict[str, int] = {**heavy_totals}
    for item in records:
        coll = item.collection
        collection_totals[coll] = collection_totals.get(coll, 0) + 1
        coll_cap = _BOOTSTRAP_COLLECTION_CAPS.get(coll)
        bucket = grouped.setdefault(coll, [])
        if coll_cap is None or len(bucket) < coll_cap:
            bucket.append(serialize(item))

    # A role without employees:view_salary never receives salary figures
    # at all -- this is the Employee Directory's real data source
    # (hydrateFromServer() in app.js), so this is the one place that
    # matters most; see also list_collection_records()'s equivalent guard
    # for the direct GET /app-data/records/employees path.
    for emp_record in grouped.get("employees", []):
        _redact_employee_salary(emp_record, principal)

    # Department-scoped login (Role.department_scope): only their departments'
    # employees, tasks, leave, rota, overtime, loans... -- see department_scope.py.
    if principal.is_dept_scoped:
        dept_index = build_index(db, principal.company_id)
        for coll in list(grouped):
            if coll in SCOPED_COLLECTIONS:
                grouped[coll] = [r for r in grouped[coll] if record_in_scope(principal, dept_index, coll, r)]

    truncated = [c for c, total in collection_totals.items() if _BOOTSTRAP_COLLECTION_CAPS.get(c) and total > _BOOTSTRAP_COLLECTION_CAPS[c]]

    # Company-wide audit trail is never sent to an Employee principal —
    # separate from the module allowlist above since it isn't collection-
    # scoped in the same way (AuditLog is its own table, not AppDataRecord).
    audit: list[dict[str, Any]] = []
    if principal.is_admin:
        audit_rows = (
            db.query(AuditLog)
            .filter(AuditLog.company_id == principal.company_id)
            .order_by(AuditLog.created_at.desc())
            .limit(50)
            .all()
        )
        # Every row previously showed principal.display_name — whoever is
        # CURRENTLY viewing the page, not who actually performed each
        # historical action (export_all_data() below already does this
        # correctly; bootstrap()'s copy never got the same fix). Look up
        # each row's real user_id instead, same as the export endpoint.
        audit_user_ids = {row.user_id for row in audit_rows if row.user_id}
        audit_user_map = {
            u.id: u.full_name
            for u in db.query(User).filter(User.id.in_(audit_user_ids)).all()
        } if audit_user_ids else {}
        audit = [
            {
                "time": row.created_at.strftime("%d/%m/%Y, %H:%M") if row.created_at else "",
                "user": audit_user_map.get(row.user_id or "", "Unknown User"),
                "action": row.action.replace("_", " ").title(),
                "record": row.module,
                "result": "Logged",
            }
            for row in audit_rows
        ]

    invoice_layout = grouped.get("invoiceLayout", [{}])[-1] if grouped.get("invoiceLayout") else None

    if principal.is_admin:
        from app.routers.companies import _resolve_company
        company = _resolve_company(principal.user)
    else:
        from app.models import Company
        company = db.query(Company).filter(Company.id == principal.company_id).first()
    company_data: dict[str, object] | None = None
    if company:
        company_data = {
            "id": company.id,
            "name": company.name,
            "trade_name": company.trade_name,
            "trn": company.trn,
            "country": company.country,
            "currency": company.currency,
            "vat_rate": str(company.vat_rate) if company.vat_rate is not None else "5.00",
            "stock_mode": company.stock_mode or "with_stock",
            "emirate": company.emirate,
            "business_type": company.business_type,
            "business_activity": company.business_activity,
            "legal_structure": company.legal_structure,
            "trade_license_no": company.trade_license_no,
            "trade_license_issue_date": company.trade_license_issue_date,
            "trade_license_expiry": company.trade_license_expiry,
            "free_zone": company.free_zone,
            "address": company.address,
            "po_box": company.po_box,
            "phone": company.phone,
            "website": company.website,
            # The raw base64 logo used to ride along in this bootstrap blob
            # too (fetched on every page load, ~780KB of it) — same fix as
            # GET /companies/current (companies.py): has_logo/logo_version
            # only, real bytes served separately and immutably-cacheable via
            # GET /companies/{id}/logo?v=<logo_version>.
            "has_logo": bool(company.logo),
            "logo_version": hashlib.sha256(company.logo.encode()).hexdigest()[:12] if company.logo else None,
            "modules_enabled": _parse_modules_enabled(company.modules_enabled),
        }

    data: dict[str, object] = {
        **grouped,
        "audit": audit,
        "invoiceLayout": invoice_layout,
        "user": {"name": principal.display_name, "role": principal.role_name if not principal.is_admin else (principal.user.role if principal.user else "admin")},
        "company": company_data,
    }
    result = {"ok": True, "data": data, "truncated_collections": truncated}
    if principal.is_admin:
        cache.set(cache_key, result, ttl=8)
    return result


@router.get("/users", dependencies=[Depends(require_company_admin)])
def list_company_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    users = (
        db.query(User)
        .filter(User.company_id == current_user.company_id)
        .order_by(User.full_name.asc())
        .all()
    )
    return {
        "ok": True,
        "users": [
            {
                "id": u.id,
                "name": u.full_name,
                "email": u.email,
                "role": u.role,
                "created_at": u.created_at.strftime("%d/%m/%Y") if u.created_at else "",
            }
            for u in users
        ],
    }


def _export_row_to_dict(obj: Any, cols: list[str]) -> dict[str, Any]:
    """Plain-JSON-safe dict for a real ORM row (Decimal/datetime aren't
    natively serializable) — the export_db_dump() SQL path already has its
    own equivalent (_v()/_esc()), this is the JSON/dict-shaped counterpart
    for export_all_data() below."""
    out: dict[str, Any] = {}
    for col in cols:
        val = getattr(obj, col, None)
        if isinstance(val, Decimal):
            val = str(val)
        elif isinstance(val, (_dt.datetime, _dt.date)):
            val = val.isoformat()
        out[col] = val
    return out


@router.get("/export", dependencies=[Depends(require_company_admin)])
@limiter.limit("10/minute")
def export_all_data(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    company_id = current_user.company_id
    records = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == company_id)
        .order_by(AppDataRecord.created_at.asc())
        .all()
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        grouped.setdefault(item.collection, []).append(serialize(item))

    # Real relational tables the AppDataRecord collections above don't
    # cover at all — Settings > Backup & Audit's category toggles (Invoices
    # & Sales / Purchases & Bills / Journal & Accounts / Payroll & HR)
    # describe this data, but until now nothing in this JSON/Excel export
    # path ever queried it (export_db_dump()'s .sql path already did, or
    # does after its own recent fix). "_db" suffix keeps these distinct
    # from any same-named AppDataRecord collection (e.g. "employees" as a
    # legacy JSON blob vs. the real Employee table here).
    invoice_cols = ["id", "invoice_number", "customer_name", "customer_trn", "issue_date",
                     "due_date", "currency", "subtotal", "vat", "total", "status", "notes"]
    invoices_db = [
        _export_row_to_dict(inv, invoice_cols)
        for inv in db.query(Invoice).filter(Invoice.company_id == company_id).order_by(Invoice.created_at.asc()).all()
    ]
    invoice_ids = [row["id"] for row in invoices_db]
    invoice_line_cols = ["id", "invoice_id", "description", "quantity", "unit_price", "vat_rate", "line_total"]
    invoice_lines_db = (
        [
            _export_row_to_dict(line, invoice_line_cols)
            for line in db.query(InvoiceLine).filter(InvoiceLine.invoice_id.in_(invoice_ids)).all()
        ]
        if invoice_ids else []
    )
    source_txn_cols = ["id", "module", "reference", "party_name", "subtotal", "vat", "total", "status"]
    source_transactions_db = [
        _export_row_to_dict(row, source_txn_cols)
        for row in db.query(SourceTransaction).filter(SourceTransaction.company_id == company_id)
        .order_by(SourceTransaction.created_at.asc()).all()
    ]
    account_cols = ["id", "code", "name", "type", "is_group", "parent_id"]
    accounts_db = [
        _export_row_to_dict(acc, account_cols)
        for acc in db.query(Account).filter(Account.company_id == company_id).order_by(Account.code.asc()).all()
    ]
    gl_cols = ["id", "entry_date", "voucher_no", "voucher_type", "account_id",
               "debit", "credit", "balance", "party", "cost_center", "narration"]
    general_ledger_db = [
        _export_row_to_dict(row, gl_cols)
        for row in db.query(GeneralLedgerEntry).filter(GeneralLedgerEntry.company_id == company_id)
        .order_by(GeneralLedgerEntry.entry_date.asc()).all()
    ]
    employee_cols = ["id", "employee_no", "full_name", "department", "designation", "basic_salary", "status"]
    employees_db = [
        _export_row_to_dict(emp, employee_cols)
        for emp in db.query(Employee).filter(Employee.company_id == company_id).all()
    ]
    payroll_run_cols = ["id", "period", "status", "gross_total", "deductions_total", "net_total"]
    payroll_runs_db = [
        _export_row_to_dict(run, payroll_run_cols)
        for run in db.query(PayrollRun).filter(PayrollRun.company_id == company_id).order_by(PayrollRun.period.asc()).all()
    ]
    payroll_run_ids = [row["id"] for row in payroll_runs_db]
    payroll_item_cols = ["id", "run_id", "employee_id", "basic", "allowances", "overtime", "deductions", "net_pay", "wps_status"]
    payroll_items_db = (
        [
            _export_row_to_dict(item, payroll_item_cols)
            for item in db.query(PayrollItem).filter(PayrollItem.run_id.in_(payroll_run_ids)).all()
        ]
        if payroll_run_ids else []
    )

    # "Payroll & HR" promised "employee records and payroll data" but never
    # queried either of these two real Tier 1 tables -- leave_requests
    # (leave.py's own real table, no longer part of the AppDataRecord
    # bridge at all -- see loadLeaveRequests()) and attendance_details
    # (attendance_store.py, superseded the old AttendancePunch table) are
    # both core HR records a company would expect "full company data" to
    # include, on par with payroll runs and the employee master list above.
    leave_cols = ["id", "employee_id", "leave_type", "start_date", "end_date", "days", "reason", "status", "approved_by", "approved_by_employee_id", "approved_at"]
    leave_requests_db = [
        _export_row_to_dict(lr, leave_cols)
        for lr in db.query(LeaveRequest).filter(LeaveRequest.company_id == company_id).order_by(LeaveRequest.start_date.asc()).all()
    ]
    attendance_cols = [
        "id", "employee_id", "employee_name", "work_date",
        "clock_in_1", "clock_out_1", "work_seconds_1", "clock_in_2", "clock_out_2", "work_seconds_2",
        "clock_in_3", "clock_out_3", "work_seconds_3", "clock_in_4", "clock_out_4", "work_seconds_4",
        "clock_in_5", "clock_out_5", "work_seconds_5", "total_seconds", "ot_seconds", "under_seconds", "session_count",
    ]
    attendance_details_db = [
        _export_row_to_dict(a, attendance_cols)
        for a in db.query(AttendanceDetail).filter(AttendanceDetail.company_id == company_id).order_by(AttendanceDetail.work_date.asc()).all()
    ]

    audit_rows = (
        db.query(AuditLog)
        .filter(AuditLog.company_id == current_user.company_id)
        .order_by(AuditLog.created_at.desc())
        .all()
    )

    user_map: dict[str, str] = {}
    for u in db.query(User).filter(User.company_id == current_user.company_id).all():
        user_map[u.id] = u.full_name

    audit = [
        {
            "time": row.created_at.strftime("%d/%m/%Y, %H:%M") if row.created_at else "",
            "user": user_map.get(row.user_id or "", current_user.full_name),
            "action": row.action.replace("_", " ").title(),
            "record": row.module,
            "result": "Logged",
        }
        for row in audit_rows
    ]

    users = [
        {
            "id": u.id,
            "name": u.full_name,
            "email": u.email,
            "role": u.role,
            "created_at": u.created_at.strftime("%d/%m/%Y") if u.created_at else "",
        }
        for u in db.query(User).filter(User.company_id == current_user.company_id).all()
    ]

    return {
        "ok": True,
        "meta": {
            "exported_at": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "exported_by": current_user.full_name,
            "company_id": current_user.company_id,
        },
        "data": {
            **grouped,
            "audit": audit,
            "users": users,
            "invoices_db": invoices_db,
            "invoice_lines_db": invoice_lines_db,
            "source_transactions_db": source_transactions_db,
            "accounts_db": accounts_db,
            "general_ledger_db": general_ledger_db,
            "employees_db": employees_db,
            "payroll_runs_db": payroll_runs_db,
            "payroll_items_db": payroll_items_db,
            "leave_requests_db": leave_requests_db,
            "attendance_details_db": attendance_details_db,
        },
    }


@router.get("/user-export/{user_id}", dependencies=[Depends(require_company_admin)])
def export_user_data(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    target = db.query(User).filter(
        User.id == user_id,
        User.company_id == current_user.company_id,
    ).first()
    if not target:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")

    audit_rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.company_id == current_user.company_id,
            AuditLog.user_id == user_id,
        )
        .order_by(AuditLog.created_at.desc())
        .all()
    )
    audit = [
        {
            "time": row.created_at.strftime("%d/%m/%Y, %H:%M") if row.created_at else "",
            "action": row.action.replace("_", " ").title(),
            "record": row.module,
            "result": "Logged",
        }
        for row in audit_rows
    ]

    return {
        "ok": True,
        "meta": {
            "exported_at": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "exported_by": current_user.full_name,
            "user_id": user_id,
        },
        "user": {
            "id": target.id,
            "name": target.full_name,
            "email": target.email,
            "role": target.role,
            "created_at": target.created_at.strftime("%d/%m/%Y") if target.created_at else "",
        },
        "audit_log": audit,
        "total_actions": len(audit),
    }


def iter_company_sql_dump(db: Session, company_id: str, exported_by: str) -> Iterator[str]:
    """Build the self-contained, restorable SQL backup for one company.

    Shared by the company owner's own Download Backup (export_db_dump()
    below) and Superadmin's per-company / bulk-all-companies backup
    endpoints (routers/superadmin.py) — one source of truth for what a
    "full backup" actually contains, so a table added to one can't be
    forgotten in the other.

    Yields the SQL piece by piece. app_data_records (where uploaded documents live, often tens
    of MB per company) is read in batches and released as it goes -- building the whole file as
    one string used to cost ~5x its size in memory (+200 MB for a 40 MB backup).
    """
    now_str = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # ── fetch ALL data while the session is still open ────────────────
    adr_count = db.query(func.count(AppDataRecord.id)).filter(AppDataRecord.company_id == company_id).scalar() or 0
    rows_inv = (
        db.query(Invoice)
        .filter(Invoice.company_id == company_id)
        .order_by(Invoice.created_at.asc())
        .all()
    )
    inv_ids = [r.id for r in rows_inv]
    rows_il = (
        db.query(InvoiceLine).filter(InvoiceLine.invoice_id.in_(inv_ids)).all()
        if inv_ids else []
    )
    rows_acc = (
        db.query(Account)
        .filter(Account.company_id == company_id)
        .order_by(Account.code.asc())
        .all()
    )
    rows_emp = (
        db.query(Employee).filter(Employee.company_id == company_id).all()
    )
    rows_audit = (
        db.query(AuditLog)
        .filter(AuditLog.company_id == company_id)
        .order_by(AuditLog.created_at.asc())
        .all()
    )
    rows_st = (
        db.query(SourceTransaction)
        .filter(SourceTransaction.company_id == company_id)
        .order_by(SourceTransaction.created_at.asc())
        .all()
    )
    # "Journal & Accounts" in the Download Backup UI promises "chart of
    # accounts AND ledger entries" — accounts (rows_acc, above) covers the
    # first half, but nothing here queried the actual posted ledger before
    # this. GeneralLedgerEntry is the real posted GL (create_gl_entries_
    # from_journal(), accounting_posting.py), the same table Trial Balance/
    # Balance Sheet/GL reports read — journal_entries/journal_lines are the
    # pre-posting form of the same data, so dumping the GL avoids doubling
    # every transaction into two overlapping representations.
    rows_gl = (
        db.query(GeneralLedgerEntry)
        .filter(GeneralLedgerEntry.company_id == company_id)
        .order_by(GeneralLedgerEntry.entry_date.asc())
        .all()
    )
    # "Payroll & HR" likewise promises "payroll data", not just employee
    # master records (rows_emp, above) — payroll_runs/payroll_items were
    # never queried at all.
    rows_pr = (
        db.query(PayrollRun).filter(PayrollRun.company_id == company_id).order_by(PayrollRun.period.asc()).all()
    )
    pr_ids = [r.id for r in rows_pr]
    rows_pi = (
        db.query(PayrollItem).filter(PayrollItem.run_id.in_(pr_ids)).all()
        if pr_ids else []
    )
    # Same gap as export_all_data() above (JSON/Excel path) -- these two
    # real Tier 1 tables were never queried here either, so a "full backup"
    # SQL dump silently had no leave or attendance history in it at all.
    rows_leave = (
        db.query(LeaveRequest).filter(LeaveRequest.company_id == company_id).order_by(LeaveRequest.start_date.asc()).all()
    )
    rows_att = (
        db.query(AttendanceDetail).filter(AttendanceDetail.company_id == company_id).order_by(AttendanceDetail.work_date.asc()).all()
    )

    # ── helpers (operate on plain Python values — no DB access) ──────
    def _esc(val: Any) -> str:
        if val is None:
            return "NULL"
        # Doubling the quote alone only closes the literal safely when the
        # target Postgres has standard_conforming_strings=on (the default
        # since PG 9.1, but not universal — some legacy/managed setups still
        # run with it off). With it off, a value containing a trailing
        # backslash (fully attacker-controlled free text on plenty of the
        # columns dumped below: invoice notes, ledger narration, leave
        # reason, employee name...) turns '\' into an escaped literal quote
        # rather than a closing one, so the string never actually closes and
        # swallows whatever comes next in the INSERT as string content.
        # Blindly doubling every backslash isn't the fix either — under the
        # (default) standard_conforming_strings=on, backslash has no special
        # meaning in a plain '...' literal, so that would corrupt any value
        # that legitimately contains one. The setting-independent fix (the
        # same one pg_dump itself uses) is Postgres's E'...' escape-string
        # syntax, which always treats backslash as an escape character
        # regardless of standard_conforming_strings — only reach for it when
        # the value actually contains a backslash.
        s = str(val)
        escaped = s.replace("\\", "\\\\").replace("'", "''")
        prefix = "E" if "\\" in s else ""
        return prefix + "'" + escaped + "'"

    def _v(obj: Any, col: str) -> str:
        v = getattr(obj, col, None)
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return str(v)
        return _esc(str(v))

    def _insert(table: str, cols: list[str], obj: Any) -> str:
        vals = ", ".join(_v(obj, c) for c in cols)
        return f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({vals}) ON CONFLICT (id) DO NOTHING;\n"

    # ── emit the SQL piece by piece ──────────────────────────────────
    yield f"-- TaxFlow Database Backup\n"
    yield f"-- Company ID : {company_id}\n"
    yield f"-- Exported by: {exported_by}\n"
    yield f"-- Exported at: {now_str}\n"
    yield f"-- Restore    : psql -d <your_db> -f this_file.sql\n\n"
    yield f"BEGIN;\n\n"

    if adr_count:
        cols = ["id", "company_id", "collection", "record_key", "payload", "created_at", "updated_at"]
        yield f"-- app_data_records ({adr_count} rows)\n"
        for r in (
            db.query(AppDataRecord)
            .filter(AppDataRecord.company_id == company_id)
            .order_by(AppDataRecord.created_at.asc(), AppDataRecord.id.asc())
            .yield_per(200)
        ):
            yield _insert("app_data_records", cols, r)
            db.expunge(r)
        yield "\n"

    if rows_inv:
        cols = ["id", "company_id", "invoice_number", "customer_name", "customer_trn",
                "issue_date", "due_date", "currency", "subtotal", "vat", "total",
                "status", "notes", "created_at", "updated_at"]
        yield (f"-- invoices ({len(rows_inv)} rows)\n")
        for r in rows_inv:
            yield (_insert("invoices", cols, r))
        yield ("\n")

    if rows_il:
        cols = ["id", "invoice_id", "description", "quantity", "unit_price", "vat_rate", "line_total"]
        yield (f"-- invoice_lines ({len(rows_il)} rows)\n")
        for r in rows_il:
            yield (_insert("invoice_lines", cols, r))
        yield ("\n")

    if rows_acc:
        cols = ["id", "company_id", "code", "name", "type", "is_group", "parent_id", "created_at", "updated_at"]
        yield (f"-- accounts ({len(rows_acc)} rows)\n")
        for r in rows_acc:
            yield (_insert("accounts", cols, r))
        yield ("\n")

    # Tables dumped by reflection (every mapped column) so a schema change
    # can't silently drop a column. Order respects foreign keys; the GL
    # block below references journal_entries.
    def _dump_model(model: Any, rows: list[Any]) -> Iterator[str]:
        if not rows:
            return
        table = model.__tablename__
        cols = [c.key for c in model.__mapper__.column_attrs]
        yield f"-- {table} ({len(rows)} rows)\n"
        for r in rows:
            yield _insert(table, cols, r)
        yield "\n"

    yield from _dump_model(Branch, db.query(Branch).filter(Branch.company_id == company_id).all())
    je_rows = db.query(JournalEntry).filter(JournalEntry.company_id == company_id).all()
    yield from _dump_model(JournalEntry, je_rows)
    je_ids = [j.id for j in je_rows]
    yield from _dump_model(JournalLine, db.query(JournalLine).filter(JournalLine.journal_id.in_(je_ids)).all() if je_ids else [])
    yield from _dump_model(StockProductMapping, db.query(StockProductMapping).filter(StockProductMapping.company_id == company_id).all())
    yield from _dump_model(StockMovement, db.query(StockMovement).filter(StockMovement.company_id == company_id).all())
    yield from _dump_model(PeriodLock, db.query(PeriodLock).filter(PeriodLock.company_id == company_id).all())

    if rows_emp:
        cols = ["id", "company_id", "employee_no", "full_name", "department", "designation", "basic_salary", "status", "created_at", "updated_at"]
        yield (f"-- employees ({len(rows_emp)} rows)\n")
        for r in rows_emp:
            yield (_insert("employees", cols, r))
        yield ("\n")

    if rows_audit:
        cols = ["id", "company_id", "user_id", "action", "module", "record_id", "created_at"]
        yield (f"-- audit_logs ({len(rows_audit)} rows)\n")
        for r in rows_audit:
            yield (_insert("audit_logs", cols, r))
        yield ("\n")

    if rows_st:
        cols = ["id", "company_id", "module", "reference", "party_name", "subtotal", "vat", "total", "status", "created_at", "updated_at"]
        yield (f"-- source_transactions ({len(rows_st)} rows)\n")
        for r in rows_st:
            yield (_insert("source_transactions", cols, r))
        yield ("\n")

    if rows_gl:
        cols = ["id", "company_id", "branch_id", "entry_date", "voucher_no", "voucher_type",
                "account_id", "journal_entry_id", "journal_line_id", "debit", "credit",
                "balance", "party", "cost_center", "narration"]
        yield (f"-- general_ledger_entries ({len(rows_gl)} rows)\n")
        for r in rows_gl:
            yield (_insert("general_ledger_entries", cols, r))
        yield ("\n")

    if rows_pr:
        cols = ["id", "company_id", "branch_id", "period", "status", "gross_total", "deductions_total", "net_total", "created_at", "updated_at"]
        yield (f"-- payroll_runs ({len(rows_pr)} rows)\n")
        for r in rows_pr:
            yield (_insert("payroll_runs", cols, r))
        yield ("\n")

    if rows_pi:
        cols = ["id", "run_id", "employee_id", "basic", "allowances", "overtime", "deductions", "net_pay", "wps_status"]
        yield (f"-- payroll_items ({len(rows_pi)} rows)\n")
        for r in rows_pi:
            yield (_insert("payroll_items", cols, r))
        yield ("\n")

    if rows_leave:
        cols = ["id", "company_id", "employee_id", "leave_type", "start_date", "end_date", "days",
                "reason", "status", "approved_by", "approved_by_employee_id", "approved_at", "created_at", "updated_at"]
        yield (f"-- leave_requests ({len(rows_leave)} rows)\n")
        for r in rows_leave:
            yield (_insert("leave_requests", cols, r))
        yield ("\n")

    if rows_att:
        cols = ["id", "company_id", "employee_id", "employee_name", "work_date",
                "clock_in_1", "clock_out_1", "work_seconds_1", "clock_in_2", "clock_out_2", "work_seconds_2",
                "clock_in_3", "clock_out_3", "work_seconds_3", "clock_in_4", "clock_out_4", "work_seconds_4",
                "clock_in_5", "clock_out_5", "work_seconds_5", "total_seconds", "ot_seconds", "under_seconds",
                "session_count", "raw_events", "created_at", "updated_at"]
        yield (f"-- attendance_details ({len(rows_att)} rows)\n")
        for r in rows_att:
            yield (_insert("attendance_details", cols, r))
        yield ("\n")

    yield ("COMMIT;\n")
    yield (
        f"\n-- {adr_count} data records · {len(rows_inv)} invoices · {len(rows_acc)} accounts · "
        f"{len(rows_gl)} ledger entries · {len(rows_pr)} payroll runs · {len(rows_leave)} leave requests · "
        f"{len(rows_att)} attendance days · {len(rows_audit)} audit entries\n"
    )



def company_sql_dump_filename(company_id: str) -> str:
    return f"taxflow-db-{company_id[:8]}-{_dt.datetime.utcnow().strftime('%Y%m%d')}.sql"


def write_company_sql_dump(db: Session, company_id: str, exported_by: str, fileobj: Any) -> None:
    """Write one company's SQL backup (UTF-8) into a binary file object, piece by piece."""
    for chunk in iter_company_sql_dump(db, company_id, exported_by):
        fileobj.write(chunk.encode("utf-8"))


def sql_dump_file_response(db: Session, company_id: str, exported_by: str) -> Response:
    """Download one company's backup: written to a temp file, streamed, then deleted."""
    tmp = tempfile.NamedTemporaryFile(prefix="taxflow-backup-", suffix=".sql", delete=False)
    try:
        with tmp:
            write_company_sql_dump(db, company_id, exported_by, tmp)
    except Exception:
        os.unlink(tmp.name)
        raise
    return FileResponse(
        tmp.name,
        media_type="text/plain; charset=utf-8",
        filename=company_sql_dump_filename(company_id),
        background=BackgroundTask(os.unlink, tmp.name),
    )




@router.get("/db-dump")
@limiter.limit("5/minute")
def export_db_dump(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _backup_module: Principal = Depends(require_module("backup")),
) -> Response:
    """Return a self-contained SQL file for the current company — restorable locally.

    Gated by the company's "backup" module (Superadmin > Module Permissions)."""
    return sql_dump_file_response(db, current_user.company_id, current_user.full_name)


@router.post("")
# This is the single busiest write endpoint in the app — every module's save
# goes through it (the "compatibility bridge" architecture). Was 180/minute,
# which a bulk purchase upload could exceed on its own: each uploaded file
# fires 3 calls here (save the raw document, extract, re-save after
# extraction) — a batch of ~60 files tripped 429s that read to users as
# bulk upload being "restricted". This endpoint isn't a credential-guessing
# surface like /auth/login, so a much higher ceiling is appropriate.
@limiter.limit("600/minute")
async def app_data_action(
    request: Request,
    action: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    # Widened from admin-only in Branch Management Phase 4 — an Employee/
    # branch login can now save/delete records here too, same as the read
    # side (list_collection_records) already was widened to. No branch
    # scoping is enforced yet: every write still lands company-wide
    # (AppDataRecord.branch_id exists as of this phase but is never read or
    # filtered on here) — that's Phases 5-6's job, once POS/Sales and
    # Inventory/Purchases are ready to consume it.
    company = resolve_principal_company(principal, db)
    payload = await request.json()
    if action == "save":
        collection = str(payload.get("collection", "app_actions"))
        assert_collection_module_enabled(db, principal, company, collection)
        assert_collection_write_permission(principal, collection)
        record = payload.get("record", {})
        if not isinstance(record, dict):
            record = {"value": record}
        record = _guard_employee_write(db, principal, collection, record)
        assert_collection_period_open(db, principal, collection, record)
        _assert_department_writable(db, principal, collection, record)
        if payload.get("create_only"):
            _assert_record_is_new(db, principal.company_id, collection, record)
        saved = save_app_record(db, principal, collection, record)
        sync_domain_model(db, principal, collection, serialize(saved))
        db.commit()
        _invalidate_write_caches(collection, principal.company_id)
        return {"ok": True, "saved": True, "id": saved.id}

    if action == "bulk-save":
        collection = str(payload.get("collection", "app_actions"))
        assert_collection_module_enabled(db, principal, company, collection)
        assert_collection_write_permission(principal, collection)
        records = payload.get("records", [])
        if not isinstance(records, list):
            records = []
        normalized_records = [record if isinstance(record, dict) else {"value": record} for record in records]
        normalized_records = [_guard_employee_write(db, principal, collection, r) for r in normalized_records]
        keys = [key for key in (record_key(collection, record) for record in normalized_records) if key]
        existing_by_key = {
            item.record_key: item
            for item in db.query(AppDataRecord)
            .filter(
                AppDataRecord.company_id == principal.company_id,
                AppDataRecord.collection == collection,
                AppDataRecord.record_key.in_(keys),
            )
            .all()
        } if keys else {}
        saved_count = 0
        updated_count = 0
        created_count = 0
        dept_index = build_index(db, principal.company_id) if principal.is_dept_scoped and collection in SCOPED_COLLECTIONS else None
        for record in normalized_records:
            assert_collection_period_open(db, principal, collection, record)
            key = record_key(collection, record)
            existing = existing_by_key.get(key) if key else None
            if collection == "employees":
                _keep_stored_employee_photo(record, existing)
            payload_json = json.dumps(record, ensure_ascii=False, default=str)
            _assert_branch_writable(principal, collection, existing)
            assert_record_writable(db, principal, collection, record, _stored_payload(existing), dept_index)
            if existing:
                existing.payload = payload_json
                saved = existing
                updated_count += 1
            else:
                saved = AppDataRecord(
                    company_id=principal.company_id,
                    branch_id=str(record.get("branch_id") or "").strip() or principal.branch_id,
                    collection=collection,
                    record_key=key,
                    payload=payload_json,
                )
                db.add(saved)
                if key:
                    existing_by_key[key] = saved
                created_count += 1
            sync_domain_model(db, principal, collection, record)
            saved_count += 1
        log_action(
            db,
            principal,
            collection,
            "records_bulk_saved",
            {"count": saved_count, "created": created_count, "updated": updated_count},
        )
        db.commit()
        _invalidate_write_caches(collection, principal.company_id)
        return {"ok": True, "saved": saved_count, "created": created_count, "updated": updated_count}

    if action == "delete":
        collection = str(payload.get("collection", "app_actions"))
        assert_collection_delete_permission(principal, collection)
        record = payload.get("record", {})
        if not isinstance(record, dict):
            record = {"value": record}
        key = record_key(collection, record)
        deleted = False
        if key:
            existing = (
                db.query(AppDataRecord)
                .filter(
                    AppDataRecord.company_id == principal.company_id,
                    AppDataRecord.collection == collection,
                    AppDataRecord.record_key == key,
                )
                .first()
            )
            if existing:
                _assert_branch_writable(principal, collection, existing)
                assert_record_writable(db, principal, collection, None, _stored_payload(existing))
                # Check the period lock against the STORED record's own date,
                # not whatever the client's delete request happens to include
                # — otherwise omitting the date field would bypass the lock.
                try:
                    stored_record = json.loads(existing.payload or "{}")
                except (TypeError, json.JSONDecodeError):
                    stored_record = record
                assert_collection_period_open(db, principal, collection, stored_record if isinstance(stored_record, dict) else record)
                db.delete(existing)
                deleted = True
            sync_domain_delete(db, principal, collection, record)
        log_action(db, principal, collection, "record_deleted" if deleted else "delete_not_found", record)
        db.commit()
        _invalidate_write_caches(collection, principal.company_id)
        return {"ok": True, "deleted": deleted, "key": key}

    if action == "bulk-delete":
        collection = str(payload.get("collection", "app_actions"))
        assert_collection_delete_permission(principal, collection)
        records = payload.get("records", [])
        if not isinstance(records, list):
            records = []
        records = [r if isinstance(r, dict) else {"value": r} for r in records]
        keys = [k for k in (record_key(collection, r) for r in records) if k]
        deleted_count = 0
        # A bulk DELETE skips the ORM hooks that keep invoices' amount_paid current.
        deleted_payments: list[Any] = []
        if keys:
            existing_rows = (
                db.query(AppDataRecord)
                .filter(
                    AppDataRecord.company_id == principal.company_id,
                    AppDataRecord.collection == collection,
                    AppDataRecord.record_key.in_(keys),
                )
                .all()
            )
            dept_index = build_index(db, principal.company_id) if principal.is_dept_scoped and collection in SCOPED_COLLECTIONS else None
            for row in existing_rows:
                _assert_branch_writable(principal, collection, row)
                assert_record_writable(db, principal, collection, None, _stored_payload(row), dept_index)
                try:
                    stored_record = json.loads(row.payload or "{}")
                except (TypeError, json.JSONDecodeError):
                    stored_record = {}
                assert_collection_period_open(db, principal, collection, stored_record if isinstance(stored_record, dict) else {})
                if collection == "payments":
                    deleted_payments.append(stored_record)
            deleted_count = (
                db.query(AppDataRecord)
                .filter(
                    AppDataRecord.company_id == principal.company_id,
                    AppDataRecord.collection == collection,
                    AppDataRecord.record_key.in_(keys),
                )
                .delete(synchronize_session=False)
            )
        if collection == "tasks" and deleted_count:
            _unlink_deleted_tasks(db, principal.company_id, keys)
        if deleted_payments:
            recompute_payment_targets(db, principal.company_id, deleted_payments)
        # Bulk domain cleanup for purchaseRecords
        if collection == "purchaseRecords" and records:
            refs = [
                str(r.get("ref") or r.get("invoice_no") or r.get("id") or "").strip()
                for r in records
            ]
            refs = [ref for ref in refs if ref]
            if refs:
                tx_ids = [
                    row[0] for row in db.query(SourceTransaction.id).filter(
                        SourceTransaction.company_id == principal.company_id,
                        SourceTransaction.module == "purchase",
                        SourceTransaction.reference.in_(refs),
                    ).all()
                ]
                if tx_ids:
                    journal_ids = [
                        row[0] for row in db.query(JournalEntry.id).filter(
                            JournalEntry.company_id == principal.company_id,
                            JournalEntry.source_id.in_(tx_ids),
                        ).all()
                    ]
                    if journal_ids:
                        release_bank_matches(db, GeneralLedgerEntry.journal_entry_id.in_(journal_ids))
                        db.query(GeneralLedgerEntry).filter(
                            GeneralLedgerEntry.journal_entry_id.in_(journal_ids)
                        ).delete(synchronize_session=False)
                        db.query(JournalLine).filter(
                            JournalLine.journal_id.in_(journal_ids)
                        ).delete(synchronize_session=False)
                        db.query(JournalEntry).filter(
                            JournalEntry.id.in_(journal_ids)
                        ).delete(synchronize_session=False)
                    db.query(TaxLine).filter(
                        TaxLine.company_id == principal.company_id,
                        TaxLine.source_id.in_(tx_ids),
                    ).delete(synchronize_session=False)
                    db.query(PostingJob).filter(
                        PostingJob.source_id.in_(tx_ids)
                    ).delete(synchronize_session=False)
                    db.query(SourceTransactionLine).filter(
                        SourceTransactionLine.source_id.in_(tx_ids)
                    ).delete(synchronize_session=False)
                    db.query(SourceTransaction).filter(
                        SourceTransaction.id.in_(tx_ids)
                    ).delete(synchronize_session=False)
                db.query(StockMovement).filter(
                    StockMovement.company_id == principal.company_id,
                    StockMovement.movement_type == "purchase",
                    StockMovement.reference.in_(refs),
                ).delete(synchronize_session=False)
                db.query(InventoryValuationLayer).filter(
                    InventoryValuationLayer.company_id == principal.company_id,
                    InventoryValuationLayer.source_module == "purchase",
                    InventoryValuationLayer.source_id.in_(refs),
                ).delete(synchronize_session=False)
        else:
            for r in records:
                sync_domain_delete(db, principal, collection, r)
        log_action(db, principal, collection, "records_bulk_deleted", {"count": deleted_count})
        db.commit()
        _invalidate_write_caches(collection, principal.company_id)
        return {"ok": True, "deleted": deleted_count}

    if action == "invoice-layout":
        assert_collection_write_permission(principal, "invoiceLayout")
        record = dict(payload)
        saved = save_app_record(db, principal, "invoiceLayout", record)
        log_action(db, principal, "settings", "invoice_layout_saved", record)
        db.commit()
        return {"ok": True, "layout": record, "id": saved.id}

    if action == "documents.extract":
        # Same gate as saving the result: a billed AI call needs the module the result is saved to
        # (expense receipts reuse this extractor). Without it any login -- even with Purchases off
        # -- could run AI extraction.
        target = "expenses" if payload.get("documentType") == "expense" else "purchaseRecords"
        assert_collection_module_enabled(db, principal, company, target)
        assert_collection_write_permission(principal, target)
        file = payload.get("file", {})

        def extract(job_db: Session, job_principal: Principal) -> dict[str, Any]:
            invoices = ingest_purchase_document(job_db, job_principal, file)
            _mark_purchase_duplicates(job_db, job_principal.company_id, invoices)
            log_action(
                job_db,
                job_principal,
                "documents",
                "document_extraction_requested",
                {"file": file.get("name"), "invoices": len(invoices)},
            )
            job_db.commit()
            return {"ok": True, "invoices": invoices}

        if request.query_params.get("background"):
            return _start_job(request, db, principal, "documents.extract", extract)
        # Runs in a thread: it can make a blocking OpenAI/Anthropic call (and a
        # blocking subprocess for PDF rendering) taking up to ~90s, which would
        # otherwise freeze this whole async worker's event loop for every user.
        return await run_in_threadpool(extract, db, principal)

    if action == "invoices.import":
        assert_collection_module_enabled(db, principal, company, "salesInvoices")
        assert_collection_write_permission(principal, "salesInvoices")
        file = payload.get("file", {})

        def import_invoices(job_db: Session, job_principal: Principal) -> dict[str, Any]:
            invoices = ingest_sales_invoice_document(job_db, job_principal, file)
            _mark_sales_duplicates(job_db, job_principal.company_id, invoices)
            non_error = [inv for inv in invoices if not inv.get("extraction_error")]
            log_action(job_db, job_principal, "salesInvoices", "invoice_import_requested", {"file": file.get("name"), "invoices": len(non_error)})
            job_db.commit()
            return {"ok": True, "invoices": invoices}

        if request.query_params.get("background"):
            return _start_job(request, db, principal, "invoices.import", import_invoices)
        # See documents.extract above: same blocking-AI-call concern applies here.
        return await run_in_threadpool(import_invoices, db, principal)

    return {"ok": True, "action": action}


def _existing_sales_invoice_numbers(db: Session, company_id: str, numbers: set[str]) -> set[str]:
    """Lower-cased invoice numbers (from `numbers`) the company already has, in either the
    salesInvoices records or the Invoice table -- the whole history, not just what a browser loaded."""
    wanted = {n.strip().lower() for n in numbers if n and n.strip()}
    if not wanted:
        return set()
    found = {
        str(key).strip().lower()
        for (key,) in db.query(AppDataRecord.record_key).filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "salesInvoices",
            func.lower(func.trim(AppDataRecord.record_key)).in_(wanted),
        ).all()
    }
    found.update(
        str(number).strip().lower()
        for (number,) in db.query(Invoice.invoice_number).filter(
            Invoice.company_id == company_id,
            func.lower(func.trim(Invoice.invoice_number)).in_(wanted),
        ).all()
    )
    return found


def _mark_sales_duplicates(db: Session, company_id: str, invoices: list[dict[str, Any]]) -> None:
    candidates = [inv for inv in invoices if not inv.get("extraction_error") and str(inv.get("invoice_no") or "").strip()]
    existing = _existing_sales_invoice_numbers(db, company_id, {str(inv["invoice_no"]) for inv in candidates})
    for inv in candidates:
        inv["db_checked"] = True
        if str(inv["invoice_no"]).strip().lower() in existing:
            inv["already_in_db"] = True


def _assert_record_is_new(db: Session, company_id: str, collection: str, record: dict[str, Any]) -> None:
    """save with create_only: refuse instead of overwriting a record that already uses this key."""
    key = record_key(collection, record)
    if not key:
        return
    if collection == "salesInvoices":
        taken = bool(_existing_sales_invoice_numbers(db, company_id, {key}))
    else:
        taken = db.query(AppDataRecord.id).filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == collection,
            AppDataRecord.record_key == key,
        ).first() is not None
    if taken:
        raise HTTPException(status_code=409, detail=f"{key} already exists")


def _supplier_key(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _purchase_alt_ref(invoice_no: str, supplier: Any) -> str:
    return f"{invoice_no} ({str(supplier or '').strip() or 'Supplier'})"


def _mark_purchase_duplicates(db: Session, company_id: str, invoices: list[dict[str, Any]]) -> None:
    """A purchase is a duplicate only when the SAME supplier (name or TRN) already has that
    invoice number. Another supplier's invoice with the same number gets its own key
    (suggested_ref) instead of being blocked or overwriting the stored one."""
    candidates = [inv for inv in invoices if not inv.get("extraction_error") and str(inv.get("invoice_no") or "").strip()]
    if not candidates:
        return
    keys: set[str] = set()
    for inv in candidates:
        inv_no = str(inv["invoice_no"]).strip()
        keys.update({inv_no, _purchase_alt_ref(inv_no, inv.get("supplier"))})
    stored: dict[str, dict[str, Any]] = {}
    for key, payload in db.query(AppDataRecord.record_key, AppDataRecord.payload).filter(
        AppDataRecord.company_id == company_id,
        AppDataRecord.collection == "purchaseRecords",
        AppDataRecord.record_key.in_(keys),
    ).all():
        try:
            stored[key] = json.loads(payload or "{}") or {}
        except (TypeError, ValueError):
            stored[key] = {}

    def same_supplier(rec: dict[str, Any], inv: dict[str, Any]) -> bool:
        trn_a = re.sub(r"\D", "", str(rec.get("supplier_trn") or ""))
        trn_b = re.sub(r"\D", "", str(inv.get("supplier_trn") or ""))
        if trn_a and trn_b:
            return trn_a == trn_b
        name_a, name_b = _supplier_key(rec.get("supplier")), _supplier_key(inv.get("supplier"))
        return not name_a or not name_b or name_a == name_b  # unknown supplier: stay cautious

    for inv in candidates:
        inv_no = str(inv["invoice_no"]).strip()
        alt = _purchase_alt_ref(inv_no, inv.get("supplier"))
        inv["db_checked"] = True
        if (inv_no in stored and same_supplier(stored[inv_no], inv)) or (alt in stored and same_supplier(stored[alt], inv)):
            inv["already_in_db"] = True
        elif inv_no in stored:
            inv["suggested_ref"] = alt


def _stored_payload(row: AppDataRecord | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        data = json.loads(row.payload or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _assert_department_writable(db: Session, principal: Principal, collection: str, record: dict[str, Any]) -> None:
    """save action: department scope must allow BOTH the incoming record and
    whatever is already stored under the same key."""
    if not principal.is_dept_scoped or collection not in SCOPED_COLLECTIONS:
        return
    key = record_key(collection, record)
    existing = None
    if key:
        existing = (
            db.query(AppDataRecord)
            .filter(
                AppDataRecord.company_id == principal.company_id,
                AppDataRecord.collection == collection,
                AppDataRecord.record_key == key,
            )
            .first()
        )
    assert_record_writable(db, principal, collection, record, _stored_payload(existing))


def _assert_branch_writable(principal: Principal, collection: str, existing: AppDataRecord | None) -> None:
    """Write-side counterpart to list_collection_records()'s read-side branch
    filter — previously save/delete had NO branch-ownership check at all,
    for any collection, so a branch-scoped principal could edit or delete
    another branch's record by knowing/guessing its key, even for
    collections already "branch-scoped" for reads (verified live: a second
    branch could overwrite and then delete a first branch's employeeLoans
    record, tampering with the financial figures the first branch would see
    on its own reports). Mirrors the exact resolution logic
    list_collection_records() already uses, just applied before a mutation
    instead of a filter."""
    if not existing or not existing.branch_id or not principal.branch_id:
        return
    if collection not in _BRANCH_FILTERED_COLLECTIONS:
        return
    collection_module = _COLLECTION_MODULE.get(collection)
    if collection_module and principal.can_cross_branch(collection_module):
        return
    active_branch = resolve_active_branch(principal, None)
    if active_branch and existing.branch_id == active_branch:
        return
    raise HTTPException(status_code=403, detail="This record belongs to a different branch")


def save_app_record(db: Session, principal: Principal, collection: str, record: dict[str, Any]) -> AppDataRecord:
    key = record_key(collection, record)
    existing = None
    if key:
        existing = (
            db.query(AppDataRecord)
            .filter(
                AppDataRecord.company_id == principal.company_id,
                AppDataRecord.collection == collection,
                AppDataRecord.record_key == key,
            )
            .first()
        )
    _assert_branch_writable(principal, collection, existing)
    if collection == "employees":
        _keep_stored_employee_photo(record, existing)
    payload = json.dumps(record, ensure_ascii=False, default=str)
    if existing:
        existing.payload = payload
        saved = existing
    else:
        # branch_id is stamped now (Branch Management Phase 4) even though
        # nothing filters on it yet — so records created from this point on
        # don't need a backfill once Phase 5/6 actually starts reading it.
        # An explicit branch_id in the record payload (e.g. an admin tagging
        # which branch a manually-entered sale belongs to) wins; otherwise
        # an Employee principal's own branch assignment is used.
        saved = AppDataRecord(
            company_id=principal.company_id,
            branch_id=str(record.get("branch_id") or "").strip() or principal.branch_id,
            collection=collection,
            record_key=key,
            payload=payload,
        )
        db.add(saved)
    log_action(db, principal, collection, "record_saved", record)
    return saved


def _is_credit_note_record(record: dict[str, Any]) -> bool:
    """A negative-signed return/credit-note document (POS Sales Return v1).
    Gated on sign, not just status/document_type text, so the main Sales
    module's existing hand-keyed "Sales Return" documents — which are
    stored with POSITIVE amounts today — keep their exact current behavior
    (a real Invoice row, normal posting) untouched. Widening this to match
    on status/text alone would silently change that pre-existing feature."""
    text = f"{record.get('document_type','')} {record.get('source','')} {record.get('status','')}".lower()
    return "return" in text and decimal_value(record.get("total")) < 0


def sync_domain_model(db: Session, principal: Principal, collection: str, record: dict[str, Any]) -> None:
    if collection == "products":
        code = str(record.get("code") or record.get("sku") or "").strip()
        name = str(record.get("name") or "").strip()
        if code and name:
            mapping = (
                db.query(StockProductMapping)
                .filter(StockProductMapping.company_id == principal.company_id, StockProductMapping.sku == code)
                .first()
            )
            if not mapping:
                mapping = StockProductMapping(company_id=principal.company_id, sku=code, name=name)
                db.add(mapping)
            mapping.name = name
            if not mapping.taxflow_name:
                mapping.taxflow_name = name
            supplier_name = str(record.get("supplier_name") or record.get("supplier") or "").strip()
            if supplier_name:
                mapping.supplier_name = supplier_name
            purchase_cost = decimal_value(record.get("cost") or record.get("purchase_price") or record.get("unit_cost"))
            if purchase_cost > 0:
                mapping.cost = purchase_cost
            mapping.tax_code = "ZERO" if "0" in str(record.get("vat", "")) and "5" not in str(record.get("vat", "")) else "VAT5"
            tracking = str(record.get("tracking") or "").strip()
            if tracking:
                mapping.tracking = tracking

    elif collection == "accounts":
        code = str(record.get("code") or "").strip()
        name = str(record.get("name") or "").strip()
        if code and name:
            account = (
                db.query(Account)
                .filter(Account.company_id == principal.company_id, Account.code == code)
                .first()
            )
            if not account:
                account = Account(company_id=principal.company_id, code=code, name=name, type=str(record.get("type") or "asset").lower())
                db.add(account)
            account.name = name
            account.type = str(record.get("type") or account.type).lower()

    elif collection == "salesInvoices":
        if not _is_credit_note_record(record):
            sync_sales_invoice(db, principal, record)
        else:
            # A negative-signed credit note (POS Sales Return v1) deliberately
            # skips sync_sales_invoice(): build_journal() rejects negative
            # subtotal/vat/total (raises PostingError), and creating a real
            # Invoice row here would make app_sales_invoice_records() (reports.py)
            # dedupe this app-data record out of every report in favor of that
            # Invoice row — whose own status filtering excludes "return" anyway.
            # Reports read the credit note straight from app-data instead (see
            # _is_credit_note() in reports.py). Legacy hand-keyed Sales Returns
            # in the main Sales module are stored with POSITIVE amounts and are
            # deliberately unaffected by this — see _is_credit_note_record()'s
            # own docstring.
            #
            # /tax/vat-return only ever sums TaxLine rows, which the above
            # skip means a POS refund never creates — so it correctly reduces
            # reported revenue but silently never reduced output VAT owed.
            # Close that specific gap with a standalone TaxLine (no
            # SourceTransaction/JournalEntry/GL row), which keeps "no full
            # ledger posting for returns" untouched.
            period = str(record.get("date") or "")[:7] or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m")
            ensure_credit_note_tax_line(
                db, principal.company_id,
                reference=str(record.get("invoice_no") or record.get("reference_no") or ""),
                taxable_amount=decimal_value(record.get("subtotal")),
                tax_amount=decimal_value(record.get("vat_amount")),
                period=period,
            )

    elif collection == "bills":
        reference = str(record.get("bill_no") or f"BILL-{record.get('id') or record.get('_id') or uuid4().hex[:8]}")
        sync_bill_accounting(
            db,
            company_id=principal.company_id,
            reference=reference,
            party_name=str(record.get("vendor") or ""),
            subtotal=decimal_value(record.get("subtotal")),
            vat=decimal_value(record.get("vat")),
            total=decimal_value(record.get("total")),
            lines=record.get("lines") if isinstance(record.get("lines"), list) else None,
            user_id=principal_user_id(principal),
            branch_id=str(record.get("branch_id") or "").strip() or principal.branch_id,
        )

    elif collection == "purchaseRecords":
        reference = str(record.get("ref") or record.get("invoice_no") or f"PURCHASE-{record.get('id') or record.get('_id') or uuid4().hex[:8]}")
        sync_purchase_accounting(
            db,
            company_id=principal.company_id,
            reference=reference,
            party_name=str(record.get("supplier") or ""),
            subtotal=decimal_value(record.get("net_amount") or record.get("subtotal")),
            vat=decimal_value(record.get("tax_amount") or record.get("vat_amount")),
            total=decimal_value(record.get("total")),
            lines=_purchase_posting_lines(db, principal, record),
            user_id=principal_user_id(principal),
            branch_id=str(record.get("branch_id") or "").strip() or principal.branch_id,
        )
        sync_purchase_stock(db, principal, record, reference)

    elif collection == "posSales":
        reference = str(record.get("receipt_no") or record.get("id") or "POS")
        sync_pos_stock(db, principal, record, reference)

    elif collection == "lockedPeriods":
        period = str(record.get("id") or "").strip()[:7]
        if len(period) == 7:
            lock = (
                db.query(PeriodLock)
                .filter(PeriodLock.company_id == principal.company_id, PeriodLock.module == "accounting", PeriodLock.period == period)
                .first()
            )
            if not lock:
                lock = PeriodLock(company_id=principal.company_id, module="accounting", period=period)
                db.add(lock)
            locked = bool(record.get("locked"))
            lock.status = "locked" if locked else "open"
            lock.locked_by = principal_user_id(principal) if locked else None
            lock.locked_at = _dt.datetime.now(_dt.timezone.utc) if locked else None

    elif collection == "payments":
        amount = decimal_value(record.get("amount"))
        if amount > 0:
            reference = str(record.get("ref") or f"PAYMENT-{record.get('id') or record.get('_id') or uuid4().hex[:8]}")
            sync_receipt_payment_accounting(
                db,
                company_id=principal.company_id,
                is_supplier=str(record.get("type") or "").strip().lower() == "supplier payment",
                reference=reference,
                party_name=str(record.get("contact") or ""),
                amount=amount,
                user_id=principal_user_id(principal),
                branch_id=str(record.get("branch_id") or "").strip() or principal.branch_id,
            )

    elif collection in ("employees", "staff"):
        emp_no = str(record.get("id") or record.get("employee_no") or record.get("emp_no") or "").strip()
        full_name = str(record.get("name") or record.get("full_name") or "").strip()
        if emp_no and full_name:
            emp = (
                db.query(Employee)
                .filter(Employee.company_id == principal.company_id, Employee.employee_no == emp_no)
                .first()
            )
            if not emp:
                emp = Employee(company_id=principal.company_id, employee_no=emp_no, full_name=full_name)
                db.add(emp)
            emp.full_name = full_name
            emp.department = str(record.get("department") or emp.department or "Operations").strip()
            emp.designation = str(record.get("designation") or record.get("position") or emp.designation or "Staff").strip()
            # A role denied employees:view_salary must not be able to
            # change salary/allowances either -- otherwise hiding these
            # fields client-side (openEmpEdit/saveEmployee in app.js)
            # would still submit whatever the hidden inputs were last
            # pre-filled with (0, since the read side now nulls them out
            # for this same role), silently wiping a real stored value on
            # every unrelated save (e.g. editing just the department).
            if principal.has("employees:view_salary"):
                if "salary" in record or "basic_salary" in record:
                    salary = decimal_value(record.get("salary") or record.get("basic_salary") or 0)
                    if salary < 0:
                        raise HTTPException(status_code=422, detail="Salary cannot be negative")
                    if salary > 0:
                        emp.basic_salary = salary
                for field_name, attr in (
                    ("housing_allowance", "housing_allowance"),
                    ("transport_allowance", "transport_allowance"),
                    ("other_allowance", "other_allowance"),
                ):
                    if field_name in record:
                        amount_value = decimal_value(record.get(field_name) or 0)
                        if amount_value < 0:
                            raise HTTPException(status_code=422, detail="Allowances cannot be negative")
                        setattr(emp, attr, amount_value)
            if "iban" in record:
                iban = str(record.get("iban") or "").replace(" ", "").upper()
                if iban and not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
                    raise HTTPException(status_code=422, detail="IBAN format is invalid")
                emp.iban = iban or None
            photo = str(record.get("photo") or "").strip()
            if photo:
                emp.photo = photo
            branch_id = str(record.get("branch_id") or "").strip()
            if branch_id:
                emp.branch_id = branch_id
            status_raw = str(record.get("status") or "Active").lower()
            emp.status = "active" if status_raw in ("active", "1", "true") else "inactive"

    elif collection == "attendanceCorrections":
        # Previously a pure status-flip: approveCorrection() (app.js) saved
        # {id,status:'Approved'} and nothing else ever happened, despite the
        # UI's own copy claiming "Approved corrections update attendance."
        # Applying it for real means inserting the requested check-in/
        # check-out as real attendance events once the request reaches
        # "Approved" - the same attendance_store path biometric/manual
        # punches already write through, so it shows up in real attendance
        # aggregation.
        if str(record.get("status") or "").strip().lower() == "approved":
            correction_id = str(record.get("id") or "").strip()
            emp_no = str(record.get("employee_id") or "").strip()
            emp_name = str(record.get("employee") or "").strip()
            if not emp_no and emp_name:
                emp = (
                    db.query(Employee)
                    .filter(Employee.company_id == principal.company_id, Employee.full_name == emp_name)
                    .first()
                )
                if emp:
                    emp_no = emp.employee_no
            punch_date = str(record.get("date") or "").strip()
            if correction_id and emp_no and punch_date:
                company_country = db.query(Company.country).filter(Company.id == principal.company_id).scalar()
                attendance_offset = timezone_utils.company_utc_offset(company_country)
                # Delete-then-recreate by (employee, date, source) so
                # re-approving after an edit, or approving twice, always
                # converges to the request's current values instead of
                # accumulating duplicate punches. Consolidated onto the
                # same shared attendance_store primitives BioTime sync and
                # the device webhook path use, instead of a third
                # independent insert/dedupe implementation.
                attendance_store.remove_events_by_source(
                    db, principal.company_id, emp_no, punch_date, "correction",
                )
                for time_field, direction in (("checkin", "in"), ("checkout", "out")):
                    time_str = str(record.get(time_field) or "").strip()
                    if not time_str:
                        continue
                    try:
                        local_dt = _dt.datetime.strptime(f"{punch_date} {time_str}", "%Y-%m-%d %H:%M")
                    except ValueError:
                        continue
                    punch_time = (local_dt - attendance_offset).replace(tzinfo=_dt.timezone.utc)
                    attendance_store.upsert_attendance_event(
                        db,
                        company_id=principal.company_id,
                        employee_id=emp_no,
                        punch_time=punch_time,
                        direction=direction,
                        employee_name=emp_name or None,
                        source="correction",
                    )

    elif collection == "rotaSwaps":
        # Previously a pure status-flip: approveRotaRow() saved the swap
        # record with a new status and nothing else - the rota board itself
        # never changed. Applying it for real means swapping the two
        # referenced rotaAssignments records' employee between each other.
        if str(record.get("status") or "").strip().lower() == "approved":
            assignment_a_id = str(record.get("assignment_a_id") or "").strip()
            assignment_b_id = str(record.get("assignment_b_id") or "").strip()
            if assignment_a_id and assignment_b_id and assignment_a_id != assignment_b_id:
                row_a = (
                    db.query(AppDataRecord)
                    .filter(
                        AppDataRecord.company_id == principal.company_id,
                        AppDataRecord.collection == "rotaAssignments",
                        AppDataRecord.record_key == assignment_a_id,
                    )
                    .first()
                )
                row_b = (
                    db.query(AppDataRecord)
                    .filter(
                        AppDataRecord.company_id == principal.company_id,
                        AppDataRecord.collection == "rotaAssignments",
                        AppDataRecord.record_key == assignment_b_id,
                    )
                    .first()
                )
                if row_a and row_b:
                    try:
                        data_a = json.loads(row_a.payload or "{}")
                        data_b = json.loads(row_b.payload or "{}")
                    except (TypeError, json.JSONDecodeError):
                        data_a = data_b = None
                    # Idempotency: by the time sync_domain_model() runs, this
                    # swap request's own row already has the new "Approved"
                    # status saved — there's no separate "was it already
                    # approved before this call" signal available. Detect an
                    # already-applied swap by checking whether A's
                    # assignment already holds B's original employee (and
                    # vice versa) — a second approve click (or a re-save of
                    # an already-approved record) would otherwise swap the
                    # two right back.
                    already_applied = (
                        isinstance(data_a, dict) and isinstance(data_b, dict)
                        and record.get("employee_b_id") and data_a.get("employee_id") == record.get("employee_b_id")
                        and record.get("employee_a_id") and data_b.get("employee_id") == record.get("employee_a_id")
                    )
                    if isinstance(data_a, dict) and isinstance(data_b, dict) and not already_applied:
                        # Only the employee-identity fields swap — the shift
                        # itself (date/type/times) stays put on each
                        # assignment row, so what actually moves is who is
                        # working it, exactly what a shift swap means.
                        for field in ("employee_id", "employee_name", "role", "department", "location"):
                            data_a[field], data_b[field] = data_b.get(field), data_a.get(field)
                        row_a.payload = json.dumps(data_a, ensure_ascii=False, default=str)
                        row_b.payload = json.dumps(data_b, ensure_ascii=False, default=str)

    elif collection == "audit":
        log_action(db, principal, str(record.get("record") or "audit"), str(record.get("action") or "ui_action"), record)


def company_tracks_stock(db: Session, company_id: str) -> bool:
    """False for a Super Admin "without stock" company: purchases, sales and POS never move stock
    (and never mint inventory items), so selling never needs stock on hand."""
    mode = db.query(Company.stock_mode).filter(Company.id == company_id).scalar()
    return mode != "without_stock"


def company_uses_perpetual_inventory(db: Session, company_id: str) -> bool:
    """Stock purchases post to 1200 Inventory and sales post their cost (Dr 5000 / Cr 1200)."""
    row = db.query(Company.stock_mode, Company.inventory_accounting).filter(Company.id == company_id).first()
    return bool(row) and row[0] != "without_stock" and (row[1] or "periodic") == "perpetual"


def _ledger_codes(db: Session, company_id: str) -> dict[str, str]:
    """Posting ledger name (lower-case) -> account code."""
    return {
        str(name or "").strip().lower(): code
        for name, code in db.query(Account.name, Account.code).filter(
            Account.company_id == company_id, Account.is_group.isnot(True),
        ).all()
    }


def _category_adds_to_stock(category: Any, ledger_codes: dict[str, str]) -> bool:
    """A line categorised to a ledger other than Inventory (an expense, a service...) isn't stock.
    Inventory, Uncategorized, blank, or free text that isn't a ledger name (e.g. a CSV "Brand"
    column mapped to category) still go to stock."""
    key = str(category or "").strip().lower()
    if key in {"", "uncategorized", "uncategorised", "inventory"} or key not in ledger_codes:
        return True
    return ledger_codes[key] == "1200"


def _purchase_posting_lines(db: Session, principal: Principal, record: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Which ledger each purchase line debits: the ledger its category names; otherwise, under
    perpetual inventory, 1200 Inventory for stock lines; otherwise the default (4000 Purchases)."""
    lines = record.get("lines")
    if not isinstance(lines, list) or not lines:
        return None
    ledger_codes = _ledger_codes(db, principal.company_id)
    perpetual = company_uses_perpetual_inventory(db, principal.company_id)
    out = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        line = dict(line)
        key = str(line.get("category") or "").strip().lower()
        if key and key not in {"uncategorized", "uncategorised"} and key in ledger_codes:
            line["account_code"] = ledger_codes[key]
        elif perpetual and _category_adds_to_stock(line.get("category"), ledger_codes):
            mapping = purchase_line_stock_mapping(db, principal, line, record, allow_create=False)
            if not mapping or mapping.tracking not in ("No", "Optional"):
                line["account_code"] = "1200"
        out.append(line)
    return out


def _item_average_cost(db: Session, company_id: str, mapping: StockProductMapping) -> Decimal:
    """Weighted average purchase cost of an item (its valuation layers), else its Item Master cost."""
    qty, value = db.query(
        func.coalesce(func.sum(InventoryValuationLayer.quantity_in), 0),
        func.coalesce(func.sum(InventoryValuationLayer.quantity_in * InventoryValuationLayer.unit_cost), 0),
    ).filter(
        InventoryValuationLayer.company_id == company_id,
        InventoryValuationLayer.item_code == mapping.sku,
        InventoryValuationLayer.quantity_in > 0,
    ).one()
    qty, value = Decimal(str(qty or 0)), Decimal(str(value or 0))
    if qty > 0:
        return value / qty
    return decimal_value(getattr(mapping, "cost", 0))


def _cogs_transactions(db: Session, company_id: str, reference: str) -> list[SourceTransaction]:
    return db.query(SourceTransaction).filter(
        SourceTransaction.company_id == company_id,
        SourceTransaction.module.in_(("cogs", "cogs_return")),
        SourceTransaction.reference == reference,
    ).all()


def _sync_cogs(db: Session, principal: Principal, stock_reference: str, cost: Decimal, is_return: bool, branch_id: str | None) -> None:
    """Post (or correct) the cost-of-sales entry for one sale/return under perpetual inventory.
    A zero cost (cancelled, back to draft, no longer perpetual) reverses what was posted;
    posted journals are never edited or deleted."""
    from app.accounting_posting import reverse_journal_entry

    reference = f"COGS-{stock_reference}"[:120]
    module = "cogs_return" if is_return else "cogs"
    cost = cost.quantize(Decimal("0.01"))
    for tx in _cogs_transactions(db, principal.company_id, reference):
        if cost > 0 and tx.module == module:
            continue
        journals = db.query(JournalEntry).filter(
            JournalEntry.company_id == principal.company_id, JournalEntry.source_module == tx.module, JournalEntry.source_id == tx.id).all()
        reversed_ids = {row[0] for row in db.query(JournalEntry.source_id).filter(
            JournalEntry.company_id == principal.company_id, JournalEntry.source_module == "reversal",
            JournalEntry.source_id.in_([j.id for j in journals])).all()} if journals else set()
        for journal in journals:
            if journal.id not in reversed_ids:
                reverse_journal_entry(db, journal, principal_user_id(principal))
        tx.subtotal = tx.total = Decimal("0")
        db.query(SourceTransactionLine).filter(SourceTransactionLine.source_id == tx.id).delete(synchronize_session=False)
    if cost <= 0:
        return
    tx = upsert_source_transaction(
        db, company_id=principal.company_id, module=module, reference=reference, party_name="",
        subtotal=cost, vat=Decimal("0"), total=cost,
        lines=[{"description": "Cost of goods sold", "account_code": "5000", "amount": cost, "quantity": 1, "unit_price": cost}],
        default_account_code="5000", branch_id=branch_id,
    )
    approve_and_post_source(db, tx, principal_user_id(principal))


def sync_purchase_stock(db: Session, principal: Principal, record: dict[str, Any], reference: str) -> None:
    lines = record.get("lines")
    if not isinstance(lines, list):
        lines = []
    branch_id = str(record.get("branch_id") or "").strip() or principal.branch_id
    allow_create_mapping = not bool(record.get("needs_product_review"))
    db.query(StockMovement).filter(
        StockMovement.company_id == principal.company_id,
        StockMovement.movement_type == "purchase",
        StockMovement.reference == reference,
    ).delete(synchronize_session=False)
    db.query(InventoryValuationLayer).filter(
        InventoryValuationLayer.company_id == principal.company_id,
        InventoryValuationLayer.source_module == "purchase",
        InventoryValuationLayer.source_id == reference,
    ).delete(synchronize_session=False)
    db.flush()
    if not company_tracks_stock(db, principal.company_id):
        return

    ledger_codes = _ledger_codes(db, principal.company_id)

    for line in lines:
        if not isinstance(line, dict):
            continue
        quantity = decimal_value(line.get("quantity") or line.get("qty") or line.get("purchase_qty") or line.get("qty_invoiced"))
        if quantity <= 0:
            continue
        if not _category_adds_to_stock(line.get("category"), ledger_codes):
            continue
        mapping = purchase_line_stock_mapping(db, principal, line, record, allow_create=allow_create_mapping)
        if not mapping:
            continue
        # Item Master's "Stock Tracking: No/Optional" — the item still
        # appears in Stock Levels (nothing here deletes its existing
        # balance), but this purchase shouldn't move its quantity.
        if mapping.tracking in ("No", "Optional"):
            continue
        unit_cost = decimal_value(
            line.get("unit_cost_before_tax")
            or line.get("unit_cost")
            or line.get("purchase_unit_cost")
            or line.get("cost")
        )
        db.add(
            StockMovement(
                company_id=principal.company_id,
                branch_id=branch_id,
                mapping_id=mapping.id,
                movement_type="purchase",
                quantity=quantity,
                unit_cost=unit_cost,
                reference=reference,
            )
        )
        db.add(
            InventoryValuationLayer(
                company_id=principal.company_id,
                item_code=mapping.sku,
                source_module="purchase",
                source_id=reference,
                quantity_in=quantity,
                quantity_remaining=quantity,
                unit_cost=unit_cost,
            )
        )


def sync_pos_stock(db: Session, principal: Principal, record: dict[str, Any], reference: str) -> None:
    """POS sales previously never reached the real StockMovement table at
    all — pos.html wrote its per-line stock deduction to a generic
    'stockMovements' AppDataRecord collection with no sync_domain_model
    branch behind it, so Inventory > Stock Levels (SUM(StockMovement.
    quantity), see list_stock_levels() in inventory.py) never reflected a
    single POS sale. Mirrors sync_purchase_stock()'s delete-then-recreate-
    by-reference pattern, but with negative quantities (a sale consumes
    stock, a purchase adds it) and no InventoryValuationLayer — nothing in
    this codebase currently consumes purchase valuation layers for COGS
    reporting either, so adding sale-side layers here would be a new,
    separate feature, not a fix for the reported "stock never decrements"
    symptom.

    Held orders (saveAs('draft'/'suspended')) write to this same collection
    but haven't actually sold anything yet — skip them entirely so stock
    isn't decremented until a sale genuinely completes. A POS refund
    (Sales Return v1) reuses this same function with quantity sign flipped
    instead of a near-duplicate sibling: its reference is namespaced
    ('RET-'+original receipt), so the delete-by-reference cleanup below
    can't touch the original sale's own movements."""
    if str(record.get("status") or "").lower() in ("draft", "suspended"):
        return
    items = record.get("items")
    if not isinstance(items, list):
        items = []
    branch_id = str(record.get("branch_id") or "").strip() or principal.branch_id
    is_return = str(record.get("type") or "").lower() == "refund" or decimal_value(record.get("total")) < 0
    movement_type = "pos_return" if is_return else "pos_sale"
    sign = Decimal("1") if is_return else Decimal("-1")
    blocked_stock = _negative_stock_blocked(db, principal.company_id)
    db.query(StockMovement).filter(
        StockMovement.company_id == principal.company_id,
        StockMovement.movement_type == movement_type,
        StockMovement.reference == reference,
    ).delete(synchronize_session=False)
    db.flush()
    perpetual = company_uses_perpetual_inventory(db, principal.company_id)
    if not company_tracks_stock(db, principal.company_id):
        _sync_cogs(db, principal, reference, Decimal("0"), is_return, branch_id)
        return
    cost_total = Decimal("0")

    for item in items:
        if not isinstance(item, dict):
            continue
        quantity = decimal_value(item.get("qty") or item.get("quantity"))
        if quantity <= 0:
            continue
        # purchase_line_stock_mapping() already falls back to "code"/"name"
        # (POS cart items' field names) when "sku"/"product" aren't present,
        # so it's directly reusable here without a POS-specific variant.
        mapping = purchase_line_stock_mapping(db, principal, item, record)
        if not mapping:
            continue
        # Item Master's "Stock Tracking: No/Optional" — sell it normally,
        # just don't deduct/return quantity for it.
        if mapping.tracking in ("No", "Optional"):
            continue
        if not is_return:
            _assert_stock_available(db, principal.company_id, mapping, quantity, blocked_stock)
        if perpetual:
            cost_total += quantity * _item_average_cost(db, principal.company_id, mapping)
        unit_cost = decimal_value(item.get("price") or item.get("unit_cost"))
        db.add(
            StockMovement(
                company_id=principal.company_id,
                branch_id=branch_id,
                mapping_id=mapping.id,
                movement_type=movement_type,
                quantity=sign * quantity,
                unit_cost=unit_cost,
                reference=reference,
            )
        )
        if not is_return:
            consume_valuation_layers(db, principal.company_id, mapping.sku, quantity)
    _sync_cogs(db, principal, reference, cost_total if perpetual else Decimal("0"), is_return, branch_id)


def purchase_line_stock_mapping(
    db: Session,
    principal: Principal,
    line: dict[str, Any],
    record: dict[str, Any],
    allow_create: bool = True,
) -> StockProductMapping | None:
    sku = str(line.get("sku") or line.get("code") or "").strip()
    product = str(line.get("product") or line.get("name") or line.get("description") or "").strip()
    if not sku and not product:
        return None
    mapping = None
    if sku:
        mapping = (
            db.query(StockProductMapping)
            .filter(StockProductMapping.company_id == principal.company_id, StockProductMapping.sku == sku)
            .first()
        )
    if not mapping and product:
        mapping = (
            db.query(StockProductMapping)
            .filter(StockProductMapping.company_id == principal.company_id, StockProductMapping.name == product)
            .first()
        )
    if not mapping:
        # A low-confidence/error-flagged AI extraction (needs_product_review
        # on the purchase record) can't be trusted enough to silently mint a
        # brand-new Inventory item from its possibly-wrong product text —
        # the purchase itself still saves, this line just stays unmapped
        # until a person picks or creates the real product.
        if not allow_create:
            return None
        mapping = StockProductMapping(
            company_id=principal.company_id,
            sku=sku or product[:60],
            name=product or sku,
            supplier_name=str(record.get("supplier") or "").strip() or None,
            mapping_confirmed=False,
        )
        db.add(mapping)
        db.flush()
    # Once a user has explicitly confirmed a mapping (saved it from the
    # Stock Mapping screen), later purchases referencing the same SKU/name
    # must not silently overwrite the name/supplier they curated — this was
    # the cause of mappings drifting out of sync with taxflow_name and
    # showing a false "Mapped" badge from the resulting text mismatch, and
    # of a user's chosen display name randomly changing on a later upload.
    # Still-unconfirmed (auto-created) rows keep refreshing from the latest
    # purchase text, same as before, until someone actually reviews them.
    if not mapping.mapping_confirmed:
        if product:
            mapping.name = product
        supplier = str(record.get("supplier") or "").strip()
        if supplier:
            mapping.supplier_name = supplier
    unit_cost = decimal_value(line.get("unit_cost_before_tax") or line.get("unit_cost") or line.get("cost"))
    if unit_cost > 0:
        mapping.cost = unit_cost
    return mapping


def _delete_source_transaction_cascade(db: Session, company_id: str, tx_id: str) -> None:
    journal_ids = [
        row[0] for row in db.query(JournalEntry.id).filter(
            JournalEntry.company_id == company_id,
            JournalEntry.source_id == tx_id,
        ).all()
    ]
    # Reversals made when an edited record was reposted point at the journal they reverse, not
    # at the transaction -- delete them too, or they'd linger with nothing to cancel.
    if journal_ids:
        journal_ids += [
            row[0] for row in db.query(JournalEntry.id).filter(
                JournalEntry.company_id == company_id,
                JournalEntry.source_module == "reversal",
                JournalEntry.source_id.in_(journal_ids),
            ).all()
        ]
    if journal_ids:
        release_bank_matches(db, GeneralLedgerEntry.journal_entry_id.in_(journal_ids))
        db.query(GeneralLedgerEntry).filter(
            GeneralLedgerEntry.journal_entry_id.in_(journal_ids)
        ).delete(synchronize_session=False)
        db.query(JournalLine).filter(
            JournalLine.journal_id.in_(journal_ids)
        ).delete(synchronize_session=False)
        db.query(JournalEntry).filter(
            JournalEntry.id.in_(journal_ids)
        ).delete(synchronize_session=False)
    db.query(TaxLine).filter(
        TaxLine.company_id == company_id,
        TaxLine.source_id == tx_id,
    ).delete(synchronize_session=False)
    db.query(PostingJob).filter(
        PostingJob.source_id == tx_id
    ).delete(synchronize_session=False)
    db.query(SourceTransactionLine).filter(
        SourceTransactionLine.source_id == tx_id
    ).delete(synchronize_session=False)
    db.query(SourceTransaction).filter(
        SourceTransaction.id == tx_id
    ).delete(synchronize_session=False)


def _unlink_deleted_tasks(db: Session, company_id: str, task_ids: list[str]) -> None:
    """Drop deleted tasks from the Rota shifts they were attached to (rotaAssignments[].tasks),
    so HRMS Rota and the ESS rota stop showing a task that no longer exists."""
    ids = {str(t) for t in task_ids if t}
    if not ids:
        return
    rows = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "rotaAssignments",
            or_(*[AppDataRecord.payload.contains(t) for t in ids]),
        )
        .all()
    )
    for row in rows:
        try:
            data = json.loads(row.payload or "{}")
        except (TypeError, ValueError):
            continue
        tasks = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(tasks, list):
            continue
        kept = [t for t in tasks if not (isinstance(t, dict) and str(t.get("task_id") or "") in ids)]
        if len(kept) != len(tasks):
            data["tasks"] = kept
            row.payload = json.dumps(data, ensure_ascii=False, default=str)
    cache.delete(f"ess_appdata:{company_id}:rotaAssignments")


def sync_domain_delete(db: Session, principal: Principal, collection: str, record: dict[str, Any]) -> None:
    if collection == "tasks":
        _unlink_deleted_tasks(db, principal.company_id, [str(record.get("id") or "")])

    elif collection == "products":
        code = str(record.get("code") or record.get("sku") or record.get("id") or "").strip()
        if code:
            mapping = (
                db.query(StockProductMapping)
                .filter(StockProductMapping.company_id == principal.company_id, StockProductMapping.sku == code)
                .first()
            )
            if mapping:
                db.delete(mapping)

    elif collection == "salesInvoices":
        number = str(record.get("invoice_no") or record.get("invoice_number") or record.get("id") or "").strip()
        if number:
            # Mirror the purchaseRecords/bills/payments branch below — a
            # posted sales invoice has a SourceTransaction (module="sales")
            # with real JournalEntry/GeneralLedgerEntry/TaxLine rows behind
            # it. Deleting only the Invoice row (as this used to do) left
            # that GL posting and Output VAT permanently in place, silently
            # inflating Trial Balance, the Balance Sheet, and the actual
            # FTA-filed VAT 201 with revenue/VAT for an invoice that no
            # longer exists.
            tx = (
                db.query(SourceTransaction)
                .filter(
                    SourceTransaction.company_id == principal.company_id,
                    SourceTransaction.module == "sales",
                    SourceTransaction.reference == number,
                )
                .first()
            )
            if tx:
                _delete_source_transaction_cascade(db, principal.company_id, tx.id)
            db.query(StockMovement).filter(
                StockMovement.company_id == principal.company_id,
                StockMovement.movement_type.in_(("sales_invoice", "sales_return")),
                StockMovement.reference == f"SALE-{number}",
            ).delete(synchronize_session=False)
            for cogs_tx in _cogs_transactions(db, principal.company_id, f"COGS-SALE-{number}"[:120]):
                _delete_source_transaction_cascade(db, principal.company_id, cogs_tx.id)
            invoice = (
                db.query(Invoice)
                .filter(Invoice.company_id == principal.company_id, Invoice.invoice_number == number)
                .first()
            )
            if invoice:
                db.delete(invoice)

    elif collection in {"purchaseRecords", "bills", "payments"}:
        module = {"purchaseRecords": "purchase", "bills": "purchase_bill", "payments": "payment"}[collection]
        reference = str(
            record.get("ref")
            or record.get("invoice_no")
            or record.get("bill_no")
            or record.get("reference")
            or record.get("id")
            or ""
        ).strip()
        if reference:
            tx = (
                db.query(SourceTransaction)
                .filter(
                    SourceTransaction.company_id == principal.company_id,
                    SourceTransaction.module == module,
                    SourceTransaction.reference == reference,
                )
                .first()
            )
            if tx:
                _delete_source_transaction_cascade(db, principal.company_id, tx.id)
            db.query(StockMovement).filter(
                StockMovement.company_id == principal.company_id,
                StockMovement.movement_type == module,
                StockMovement.reference == reference,
            ).delete(synchronize_session=False)
            db.query(InventoryValuationLayer).filter(
                InventoryValuationLayer.company_id == principal.company_id,
                InventoryValuationLayer.source_module == module,
                InventoryValuationLayer.source_id == reference,
            ).delete(synchronize_session=False)

    elif collection == "posSales":
        # resumeSale() (pos.html) deletes a held order's draft record after
        # loading it into the cart — sync_pos_stock() now skips drafts
        # entirely going forward, but this also cleans up any stock
        # movements a held order already accumulated before that fix, and
        # covers deleting a completed sale or refund record generally
        # (either movement_type; a given reference is only ever one or the
        # other, so filtering both by reference in one query is safe).
        reference = str(record.get("receipt_no") or record.get("id") or "").strip()
        if reference:
            db.query(StockMovement).filter(
                StockMovement.company_id == principal.company_id,
                StockMovement.movement_type.in_(("pos_sale", "pos_return")),
                StockMovement.reference == reference,
            ).delete(synchronize_session=False)
            for cogs_tx in _cogs_transactions(db, principal.company_id, f"COGS-{reference}"[:120]):
                _delete_source_transaction_cascade(db, principal.company_id, cogs_tx.id)


def _negative_stock_blocked(db: Session, company_id: str) -> bool:
    row = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "inventorySettings", AppDataRecord.record_key == "config")
        .first()
    )
    if not row:
        return False
    try:
        return json.loads(row.payload or "{}").get("allow_negative_stock") is False
    except (TypeError, json.JSONDecodeError):
        return False


def _assert_stock_available(db: Session, company_id: str, mapping: StockProductMapping, quantity: Decimal, blocked: bool) -> None:
    """Only enforced when the company has switched negative stock off
    (Inventory > Adjustments toggle); default stays permissive."""
    if not blocked:
        return
    on_hand = (
        db.query(func.coalesce(func.sum(StockMovement.quantity), 0))
        .filter(StockMovement.company_id == company_id, StockMovement.mapping_id == mapping.id)
        .scalar()
    )
    if Decimal(str(on_hand or 0)) - quantity < 0:
        raise HTTPException(status_code=409, detail=f"Insufficient stock for {mapping.name} ({Decimal(str(on_hand or 0)):g} on hand)")


def sync_sales_invoice_stock(db: Session, principal: Principal, record: dict[str, Any], invoice: Invoice) -> None:
    """Issued/paid sales invoices consume tracked stock (POS sales are handled
    separately via posSales, so POS-sourced invoices are skipped here).
    Delete-then-recreate by reference keeps re-saves idempotent, and moving an
    invoice back to draft/cancelled returns the stock."""
    if str(record.get("source") or "").lower() == "pos":
        return
    reference = f"SALE-{invoice.invoice_number}"
    doc_text = f"{record.get('document_type','')} {record.get('source','')} {record.get('status','')}".lower()
    is_return = "return" in doc_text
    movement_type = "sales_return" if is_return else "sales_invoice"
    db.query(StockMovement).filter(
        StockMovement.company_id == principal.company_id,
        StockMovement.movement_type.in_(("sales_invoice", "sales_return")),
        StockMovement.reference == reference,
    ).delete(synchronize_session=False)
    branch_id = str(record.get("branch_id") or "").strip() or principal.branch_id
    perpetual = company_uses_perpetual_inventory(db, principal.company_id)
    if invoice.status in ("draft", "cancelled") or not company_tracks_stock(db, principal.company_id):
        _sync_cogs(db, principal, reference, Decimal("0"), is_return, branch_id)
        return
    db.flush()
    sign = Decimal("1") if is_return else Decimal("-1")
    blocked = _negative_stock_blocked(db, principal.company_id)
    cost_total = Decimal("0")
    for line in record.get("lines") if isinstance(record.get("lines"), list) else []:
        if not isinstance(line, dict):
            continue
        quantity = abs(decimal_value(line.get("qty") or line.get("quantity")))
        if quantity <= 0:
            continue
        item = {
            "sku": line.get("product_code") or line.get("sku") or line.get("code"),
            "product": line.get("product_name") or line.get("product") or line.get("description"),
        }
        mapping = purchase_line_stock_mapping(db, principal, item, record, allow_create=False)
        if not mapping or mapping.tracking in ("No", "Optional"):
            continue
        if not is_return:
            _assert_stock_available(db, principal.company_id, mapping, quantity, blocked)
        if perpetual:
            cost_total += quantity * _item_average_cost(db, principal.company_id, mapping)
        db.add(StockMovement(
            company_id=principal.company_id,
            branch_id=branch_id,
            mapping_id=mapping.id,
            movement_type=movement_type,
            quantity=sign * quantity,
            unit_cost=decimal_value(line.get("unit_price") or line.get("price")),
            reference=reference,
        ))
        if not is_return:
            consume_valuation_layers(db, principal.company_id, mapping.sku, quantity)
    _sync_cogs(db, principal, reference, cost_total if perpetual else Decimal("0"), is_return, branch_id)


def _record_pos_receipt(db: Session, principal: Principal, record: dict[str, Any], invoice: Invoice) -> None:
    """A POS sale is paid at the till: without a receipt its invoice would sit
    in Accounts Receivable forever and the cash/card takings would never reach
    Cash & Bank or Bank Reconciliation."""
    if str(record.get("source") or "").lower() != "pos" or invoice.status != "paid":
        return
    if str(record.get("payment_method") or "cash").lower() == "credit" or invoice.total <= 0:
        return
    receipt = {
        "type": "Customer Receipt",
        "ref": f"RCT-{invoice.invoice_number}",
        "contact": invoice.customer_name,
        "amount": float(invoice.total),
        "method": str(record.get("payment_method") or "cash"),
        "date": str(record.get("date") or ""),
        "invoice_no": invoice.invoice_number,
        "source": "POS",
    }
    saved = save_app_record(db, principal, "payments", receipt)
    sync_domain_model(db, principal, "payments", serialize(saved))


def sync_sales_invoice(db: Session, principal: Principal, record: dict[str, Any]) -> None:
    number = str(record.get("invoice_no") or record.get("invoice_number") or "").strip()
    customer = str(record.get("customer") or record.get("customer_name") or "Customer").strip()
    if not number:
        return
    invoice = (
        db.query(Invoice)
        .filter(Invoice.company_id == principal.company_id, Invoice.invoice_number == number)
        .first()
    )
    if not invoice:
        invoice = Invoice(company_id=principal.company_id, invoice_number=number, customer_name=customer)
        db.add(invoice)
    invoice.customer_name = customer
    branch_id = str(record.get("branch_id") or "").strip() or principal.branch_id
    if branch_id:
        invoice.branch_id = branch_id
    invoice.status = "issued" if str(record.get("status", "")).lower() in {"ready", "pending"} else str(record.get("status") or "draft").lower()
    company_vat_rate = get_company_vat_rate(resolve_principal_company(principal, db))
    lines = record.get("lines") if isinstance(record.get("lines"), list) else []
    if lines:
        invoice.lines = []
        for line in lines:
            quantity = decimal_value(line.get("qty") or line.get("quantity") or 1)
            unit_price = decimal_value(line.get("unit_price") or line.get("price_snapshot") or line.get("price"))
            if not unit_price:
                # Imported lines can carry only an amount; without this they posted as zero.
                line_amount = decimal_value(line.get("total") or line.get("line_total") or line.get("amount"))
                if line_amount and quantity > 0:
                    unit_price = line_amount / quantity
            invoice.lines.append(
                InvoiceLine(
                    description=str(line.get("description") or line.get("product_name") or "Invoice item"),
                    quantity=quantity if quantity > 0 else Decimal("1"),
                    unit_price=unit_price,
                    vat_rate=decimal_value(line.get("tax_rate") or line.get("vat_rate") or company_vat_rate),
                )
            )
    elif not invoice.lines:
        # No line items at all (minimal/legacy record) — nothing to
        # recompute from, fall back to the client-supplied subtotal as a
        # single line so calculate_totals() below still has something to sum.
        invoice.lines = [
            InvoiceLine(
                description=f"Imported invoice {number}",
                quantity=Decimal("1"),
                unit_price=decimal_value(record.get("subtotal")),
                vat_rate=company_vat_rate,
            )
        ]
    # Recompute subtotal/vat/total from the actual InvoiceLines server-side —
    # previously these were trusted verbatim from the client
    # (record.get("subtotal"/"vat_amount"/"total")), so a client-side bug or
    # a hardcoded-5% VAT calculation (calcLine() etc. in app.js, which
    # ignores a company's configured non-default vat_rate) would silently
    # persist a wrong total that later drives VAT return figures — even
    # though the lines built above already correctly use company_vat_rate.
    # Mirrors invoices.py's calculate_totals() for the typed REST API.
    from app.routers.invoices import calculate_totals
    calculate_totals(invoice)
    db.flush()
    # A draft invoice isn't a committed sale yet (reports.py's
    # _is_recognized_revenue_status() already excludes "draft" from every
    # revenue figure) — posting it to the GL/TaxLine here anyway meant
    # simply saving a draft-to-preview-totals immediately posted real
    # Output VAT that a delete (see sync_domain_delete above) never used to
    # reverse, leaving VAT permanently stuck in every report and in what
    # gets filed with the FTA.
    if invoice.status != "draft":
        sync_sales_invoice_accounting(db, invoice, principal_user_id(principal))
        _record_pos_receipt(db, principal, record, invoice)
    sync_sales_invoice_stock(db, principal, record, invoice)


def log_action(db: Session, principal: Principal, module: str, action: str, detail: Any) -> None:
    db.add(
        AuditLog(
            company_id=principal.company_id,
            user_id=principal.user.id if principal.user else None,
            employee_id=principal.employee.id if principal.employee else None,
            # Branch Login Phase 2 — a Branch principal has neither .user nor
            # .employee, so without this its actions would silently lose
            # their actor identity in the audit trail.
            branch_actor_id=principal.branch.id if principal.branch else None,
            module=str(module)[:60],
            action=str(action)[:80],
            detail=json.dumps(detail, ensure_ascii=False, default=str)[:1000],
        )
    )


def ingest_purchase_document(db: Session, principal: Principal, file: dict[str, Any]) -> list[dict[str, Any]]:
    name = str(file.get("name") or "purchase-upload").strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    content = decode_uploaded_file(file)
    if not content:
        return [purchase_extraction_error(name, "Uploaded file content was empty")]

    # The Upload Documents UI advertises ".zip (batch)" as a supported
    # format, but this endpoint previously had no handling for it at all —
    # any .zip fell straight into the "Unsupported purchase upload format"
    # branch below, which read to users as bulk upload being blocked/
    # restricted. Unpack it here and run every contained file through the
    # exact same per-file parsing this function already does.
    if ext == "zip":
        return _ingest_purchase_zip(db, principal, name, content)
    return _ingest_purchase_file(db, principal, name, ext, content)


# Nested zips aren't unpacked recursively — deliberately, to avoid zip-bomb-
# style surprises from an archive containing archives.
_PURCHASE_ZIP_SKIP_EXTENSIONS = {"zip"}

# Each file inside a batch .zip runs through the same AI-extraction pipeline
# as a standalone upload -- a real, billed Claude/OpenAI Vision call per
# file -- but this whole endpoint is rate-limited as a generic 600/minute
# save, not per AI call (that's a separate, coarser fix). Without a cap here,
# one HTTP request (counted once against that limit) could fan out into
# hundreds of paid API calls from a single uploaded .zip. 50 files is well
# above a normal day's batch of purchase invoices; a bigger backlog is meant
# to be uploaded as more than one batch. The per-entry size cap is a second,
# independent guard against a classic zip-bomb (a tiny archive whose entry
# decompresses to gigabytes) -- archive.read() below has no size limit of
# its own, so this must be checked from the entry's declared size before
# reading it into memory.
_PURCHASE_ZIP_MAX_ENTRIES = 50
_PURCHASE_ZIP_MAX_ENTRY_SIZE = 20 * 1024 * 1024  # 20MB — generous for a single invoice PDF/scan


def _ingest_purchase_zip(db: Session, principal: Principal, zip_name: str, content: bytes) -> list[dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return [purchase_extraction_error(zip_name, "Could not open .zip file — it may be corrupted")]

    entries = [
        info for info in archive.infolist()
        if not info.is_dir()
        and not info.filename.startswith("__MACOSX/")
        and not info.filename.rsplit("/", 1)[-1].startswith(".")
    ]
    if not entries:
        return [purchase_extraction_error(zip_name, "The .zip file contained no readable files")]

    all_results: list[dict[str, Any]] = []
    if len(entries) > _PURCHASE_ZIP_MAX_ENTRIES:
        all_results.append(purchase_extraction_error(
            zip_name,
            f"This .zip has {len(entries)} files — batches are capped at {_PURCHASE_ZIP_MAX_ENTRIES} per upload. "
            "Split it into smaller batches and upload each separately.",
        ))
        entries = entries[:_PURCHASE_ZIP_MAX_ENTRIES]

    for info in entries:
        entry_name = info.filename.rsplit("/", 1)[-1]
        entry_ext = entry_name.rsplit(".", 1)[-1].lower() if "." in entry_name else ""
        display_name = f"{zip_name}/{entry_name}"
        if entry_ext in _PURCHASE_ZIP_SKIP_EXTENSIONS:
            all_results.append(purchase_extraction_error(display_name, "Nested .zip files inside a batch upload aren't supported"))
            continue
        if info.file_size > _PURCHASE_ZIP_MAX_ENTRY_SIZE:
            all_results.append(purchase_extraction_error(display_name, "File is too large (max 20MB per document in a batch)"))
            continue
        try:
            entry_content = archive.read(info)
        except Exception as exc:
            all_results.append(purchase_extraction_error(display_name, f"Could not read file from archive: {exc}"))
            continue
        all_results.extend(_ingest_purchase_file(db, principal, display_name, entry_ext, entry_content))
    return all_results


def _ingest_purchase_file(db: Session, principal: Principal, name: str, ext: str, content: bytes) -> list[dict[str, Any]]:
    _last_ai_error.set("")
    try:
        if ext == "csv":
            rows = parse_csv_rows(content)
        elif ext in {"xlsx", "xlsm"}:
            rows = parse_xlsx_rows(content)
        elif ext == "xls":
            rows = parse_excel_html_rows(content)
        elif ext == "pdf":
            if not _has_ai_key():
                return [purchase_extraction_error(name, "AI extraction not configured — add ANTHROPIC_API_KEY or OPENAI_API_KEY to your .env file to enable PDF reading")]
            rows = parse_pdf_purchase_rows(content)
        elif ext in PURCHASE_IMAGE_EXTENSIONS:
            if not _has_ai_key():
                return [purchase_extraction_error(name, "AI extraction not configured — add ANTHROPIC_API_KEY or OPENAI_API_KEY to your .env file to enable image reading")]
            rows = parse_image_purchase_rows(content, ext)
        else:
            return [purchase_extraction_error(name, f"Unsupported purchase upload format: .{ext or 'unknown'}")]
    except Exception as exc:
        if _last_ai_error.get():
            return [purchase_extraction_error(name, f"AI reading failed: {_last_ai_error.get()}")]
        return [purchase_extraction_error(name, f"Could not parse file: {exc}")]

    invoices = merge_purchase_invoices(build_purchase_invoices_from_rows(db, principal, rows, name), name)
    if not invoices:
        if _last_ai_error.get():
            return [purchase_extraction_error(name, f"AI reading failed: {_last_ai_error.get()}")]
        hints = purchase_excel_debug_hint(content, ext)
        return [purchase_extraction_error(name, "No purchase invoice rows were found in the uploaded file" + hints)]
    return invoices


def decode_uploaded_file(file: dict[str, Any]) -> bytes:
    raw = str(file.get("base64") or "")
    if "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw)
    except Exception:
        return b""


def parse_csv_rows(content: bytes) -> list[dict[str, Any]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [normalize_purchase_row(row) for row in reader]



# .xlsx is itself a zip container, so parsing one means decompressing
# whatever's inside -- with no cap, a tiny crafted file whose entries
# declare a huge uncompressed size is a classic zip-bomb DoS. infolist()
# only reads the central directory (near-free, no decompression), so this
# check runs before any entry is actually read into memory.
_XLSX_MAX_UNCOMPRESSED_SIZE = 100 * 1024 * 1024  # 100MB — generous for any real workbook


def parse_xlsx_rows(content: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(content)) as workbook:
        total_uncompressed = sum(info.file_size for info in workbook.infolist())
        if total_uncompressed > _XLSX_MAX_UNCOMPRESSED_SIZE:
            raise ValueError("This file is too large or unusually compressed to process")
        shared_strings = read_xlsx_shared_strings(workbook)
        sheet_xml_list = [workbook.read(sheet_name) for sheet_name in xlsx_sheet_names(workbook)]
    rows: list[dict[str, Any]] = []
    for sheet_xml in sheet_xml_list:
        rows.extend(parse_xlsx_sheet_rows(sheet_xml, shared_strings))
    return rows


def parse_xlsx_sheet_rows(sheet_xml: bytes, shared_strings: list[str]) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(sheet_xml)
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    table_rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", ns):
        cells: list[str] = []
        expected_index = 0
        for cell in row.findall("x:c", ns):
            ref = str(cell.attrib.get("r") or "")
            cell_index = xlsx_column_index("".join(ch for ch in ref if ch.isalpha()))
            while expected_index < cell_index:
                cells.append("")
                expected_index += 1
            cells.append(read_xlsx_cell(cell, shared_strings, ns))
            expected_index += 1
        if any(value.strip() for value in cells):
            table_rows.append(cells)
    if not table_rows:
        return []
    header_index = detect_purchase_header_row(table_rows)
    if header_index < 0:
        return []
    headers = [normalize_header(value) for value in table_rows[header_index]]
    rows = []
    for raw in table_rows[header_index + 1:]:
        row = {headers[i]: raw[i] if i < len(raw) else "" for i in range(len(headers)) if headers[i]}
        normalized = normalize_purchase_row(row)
        if any(str(value or "").strip() for value in normalized.values()):
            rows.append(normalized)
    return rows


def detect_purchase_header_row(table_rows: list[list[str]]) -> int:
    best_index = -1
    best_score = 0
    for index, row in enumerate(table_rows[:25]):
        headers = {normalize_header(value) for value in row if str(value or "").strip()}
        score = 0
        if purchase_alias_hit(headers, "product"):
            score += 3
        if purchase_alias_hit(headers, "quantity"):
            score += 2
        if purchase_alias_hit(headers, "unit_cost") or purchase_alias_hit(headers, "line_total"):
            score += 2
        if purchase_alias_hit(headers, "invoice_no"):
            score += 1
        if purchase_alias_hit(headers, "supplier"):
            score += 1
        if purchase_alias_hit(headers, "unit"):
            score += 1
        if score > best_score:
            best_score = score
            best_index = index
    return best_index if best_score >= 4 else -1


def purchase_alias_hit(headers: set[str], field: str) -> bool:
    return any(alias in headers for alias in PURCHASE_FIELD_ALIASES[field])


def read_xlsx_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings = []
    for item in root.findall("x:si", ns):
        strings.append("".join(node.text or "" for node in item.findall(".//x:t", ns)))
    return strings


def xlsx_sheet_names(workbook: zipfile.ZipFile) -> list[str]:
    names = sorted(
        name
        for name in workbook.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )
    if not names:
        raise ValueError("workbook has no worksheets")
    return names


def xlsx_column_index(column: str) -> int:
    index = 0
    for char in column.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return max(index - 1, 0)


def read_xlsx_cell(cell: ElementTree.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    value_node = cell.find("x:v", ns)
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", ns)).strip()
    value = value_node.text if value_node is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(value)].strip()
        except (ValueError, IndexError):
            return ""
    return str(value or "").strip()


def parse_excel_html_rows(content: bytes) -> list[dict[str, Any]]:
    text = content.decode("utf-8-sig", errors="ignore")
    if "<table" not in text.lower():
        return []
    table_rows: list[list[str]] = []
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, flags=re.IGNORECASE | re.DOTALL):
        cells = []
        for cell_html in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL):
            cleaned = re.sub(r"<[^>]+>", " ", cell_html)
            cleaned = html.unescape(re.sub(r"\s+", " ", cleaned)).strip()
            cells.append(cleaned)
        if any(cells):
            table_rows.append(cells)
    header_index = detect_purchase_header_row(table_rows)
    if header_index < 0:
        return []
    headers = [normalize_header(value) for value in table_rows[header_index]]
    rows = []
    for raw in table_rows[header_index + 1:]:
        row = {headers[i]: raw[i] if i < len(raw) else "" for i in range(len(headers)) if headers[i]}
        normalized = normalize_purchase_row(row)
        if any(str(value or "").strip() for value in normalized.values()):
            rows.append(normalized)
    return rows


def parse_pdf_purchase_rows(content: bytes) -> list[dict[str, Any]]:
    ai_rows = extract_purchase_rows_with_openai(content, "pdf")
    if ai_rows:
        return ai_rows
    text = extract_pdf_text_with_pdfplumber(content) or extract_pdf_text(content)
    if not text:
        text = extract_pdf_text_with_ocr(content)
    if not text:
        return []
    amazon_rows = parse_amazon_tax_invoice_rows(text)
    if amazon_rows:
        return amazon_rows
    table_rows = parse_pdf_purchase_table_rows(text)
    if table_rows:
        return table_rows
    return purchase_rows_from_document_text(text)


def parse_pdf_purchase_table_rows(text: str) -> list[dict[str, Any]]:
    table_rows: list[list[str]] = []
    for line in (text or "").splitlines():
        cells = split_pdf_table_line(line)
        if len(cells) >= 3:
            table_rows.append(cells)
    header_index = detect_purchase_header_row(table_rows)
    if header_index < 0:
        return []
    headers = [normalize_header(value) for value in table_rows[header_index]]
    rows: list[dict[str, Any]] = []
    for raw in table_rows[header_index + 1:]:
        row = {headers[i]: raw[i] if i < len(raw) else "" for i in range(len(headers)) if headers[i]}
        normalized = normalize_purchase_row(row)
        if is_purchase_table_data_row(normalized):
            rows.append(normalized)
    return rows


def split_pdf_table_line(line: str) -> list[str]:
    cleaned = re.sub(r"[\u00a0\r]+", " ", str(line or "")).strip()
    if not cleaned:
        return []
    if "|" in cleaned:
        cells = cleaned.split("|")
    elif "\t" in cleaned:
        cells = cleaned.split("\t")
    else:
        cells = re.split(r"\s{2,}", cleaned)
    return [re.sub(r"\s+", " ", cell).strip() for cell in cells if str(cell or "").strip()]


def is_purchase_table_data_row(row: dict[str, Any]) -> bool:
    product = str(row.get("product") or row.get("sku") or row.get("category") or "").strip()
    if not product:
        return False
    if re.search(r"^(total|subtotal|sub total|vat|tax|amount due|balance)$", product, flags=re.IGNORECASE):
        return False
    return any(str(row.get(field) or "").strip() for field in ("quantity", "unit_cost", "line_total", "vat_amount"))


AMAZON_PRICE_RE = re.compile(
    r"(\d+)\s+AED\s*([\d,]+\.\d+)\s+5%\s+AED\s*([\d,]+\.\d+)\s+AED\s*([\d,]+\.\d+)\s+AED\s*([\d,]+\.\d+)"
)

AMAZON_DISCOUNT_RE = re.compile(
    r"Discount\s+-AED\s*([\d,]+\.\d+)\s+5%\s+-AED\s*([\d,]+\.\d+)\s+-AED\s*([\d,]+\.\d+)\s+-AED\s*([\d,]+\.\d+)",
    flags=re.IGNORECASE,
)


def extract_pdf_text_with_pdfplumber(content: bytes) -> str:
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except Exception:
        return ""
    try:
        chunks: list[str] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                chunks.append(page.extract_text() or "")
        return "\n".join(chunks).strip()
    except Exception:
        return ""


def parse_amazon_tax_invoice_rows(text: str) -> list[dict[str, Any]]:
    if not re.search(r"\bAmazon\b", text, flags=re.IGNORECASE):
        return []
    if not re.search(r"Tax Invoice Number|Invoice detail|VAT Summary", text, flags=re.IGNORECASE):
        return []

    header = parse_amazon_header(text)
    products = parse_amazon_products(text)
    if not products:
        return []

    rows = []
    for item in products:
        rows.append(normalize_purchase_row({
            "invoice_no": header["invoice_no"],
            "date": header["date"],
            "supplier": header["supplier"],
            "supplier_trn": header["supplier_trn"],
            "product": item["description"],
            "quantity": item["quantity"],
            "unit": "PCS",
            "unit_cost": item["unit_price_excl_vat"],
            "vat_amount": item["vat_amount"],
            "line_total": item["line_total_excl_vat"],
            "tax_type": "VAT 5%",
            "notes": "Amazon tax invoice extraction",
            "raw": item["raw"],
        }))
    return rows


def parse_amazon_header(text: str) -> dict[str, str]:
    return {
        "date": amazon_match(text, r"Tax Invoice Issue Date\s+(.+)") or "",
        "invoice_no": amazon_match(text, r"Tax Invoice Number\s+([\w\-]+)") or "AMAZON-INVOICE",
        "supplier": amazon_match(text, r"Sold by\s+(.+)") or "Amazon",
        "supplier_trn": amazon_match(text, r"VAT\s*#\s*(\d{10,})") or "",
    }


def amazon_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def parse_amazon_products(text: str) -> list[dict[str, Any]]:
    detail_match = re.search(
        r"Invoice detail\n(.+?)(?=\nTotal\s+AED|\nVAT Summary)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    detail_text = detail_match.group(1) if detail_match else text
    lines = detail_text.splitlines()
    rows: list[dict[str, Any]] = []
    index = 0

    while index < len(lines):
        line = lines[index].strip()
        if is_amazon_noise_line(line):
            index += 1
            continue

        discount_match = AMAZON_DISCOUNT_RE.search(line)
        if discount_match:
            rows.append(make_amazon_discount_row(discount_match))
            index += 1
            continue

        price_match = AMAZON_PRICE_RE.search(line)
        if price_match:
            description = " ".join((amazon_text_before_asin(line) or "").split())
            if description:
                rows.append(make_amazon_product_row(description, price_match))
            index += 1
            continue

        desc_lines: list[str] = []
        cursor = index
        while cursor < len(lines):
            current = lines[cursor].strip()
            if not current:
                cursor += 1
                continue
            if is_amazon_noise_line(current) or AMAZON_DISCOUNT_RE.search(current):
                break
            price_match = AMAZON_PRICE_RE.search(current)
            if price_match:
                extra = amazon_text_before_asin(current)
                if extra:
                    desc_lines.append(extra)
                break
            clean = re.sub(r"\|\s*[A-Z0-9]{10}.*", "", current).rstrip("|").strip()
            if clean:
                desc_lines.append(clean)
            cursor += 1

        description = " ".join(" ".join(desc_lines).split())
        if cursor < len(lines):
            price_match = AMAZON_PRICE_RE.search(lines[cursor].strip())
            if price_match and description:
                rows.append(make_amazon_product_row(description, price_match))
            index = cursor + 1
        else:
            index += 1

    return rows


def is_amazon_noise_line(line: str) -> bool:
    return (
        not line
        or line.startswith("Condition")
        or bool(re.fullmatch(r"[A-Z0-9]{10}", line))
        or ("Description" in line and "Unit Price" in line)
        or line.startswith("(excl")
        or line.startswith("(incl")
    )


def amazon_text_before_asin(line: str) -> str | None:
    part = re.sub(r"\|\s*[A-Z0-9]{10}.*", "", line).strip()
    if part and not re.match(r"^\d+\s+AED", part):
        return part
    return None


def make_amazon_product_row(description: str, match: re.Match[str]) -> dict[str, Any]:
    quantity = decimal_value(match.group(1)) or Decimal("1")
    unit_excl = decimal_value(match.group(2))
    unit_vat = decimal_value(match.group(3))
    return {
        "description": description,
        "quantity": quantity,
        "unit_price_excl_vat": unit_excl,
        "vat_amount": unit_vat * quantity,
        "line_total_excl_vat": unit_excl * quantity,
        "raw": {
            "unit_vat_amount": f"AED {match.group(3)}",
            "unit_price_incl_vat": f"AED {match.group(4)}",
            "item_subtotal_incl_vat": f"AED {match.group(5)}",
        },
    }


def make_amazon_discount_row(match: re.Match[str]) -> dict[str, Any]:
    unit_excl = -decimal_value(match.group(1))
    unit_vat = -decimal_value(match.group(2))
    return {
        "description": "Discount",
        "quantity": Decimal("1"),
        "unit_price_excl_vat": unit_excl,
        "vat_amount": unit_vat,
        "line_total_excl_vat": unit_excl,
        "raw": {
            "unit_vat_amount": f"-AED {match.group(2)}",
            "unit_price_incl_vat": f"-AED {match.group(3)}",
            "item_subtotal_incl_vat": f"-AED {match.group(4)}",
        },
    }


def parse_image_purchase_rows(content: bytes, ext: str) -> list[dict[str, Any]]:
    # Raises RuntimeError if OpenAI key is set but call fails (propagates to caller)
    ai_rows = extract_purchase_rows_with_openai(content, ext)
    if ai_rows:
        return ai_rows
    # Fallback: Tesseract OCR (no API key required)
    text = extract_image_text_with_tesseract(content, ext)
    if not text:
        raise RuntimeError("Could not extract text from image. Try uploading a clearer image or a PDF/CSV instead.")
    return purchase_rows_from_document_text(text)


PURCHASE_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "bmp", "webp", "tiff", "tif"}
OPENAI_DIRECT_IMAGE_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


OPENAI_PURCHASE_EXTRACTION_PROMPT = """You are an invoice data extraction expert.

Read this supplier purchase invoice carefully and extract all visible data.
Return ONLY a valid JSON object. Do not include markdown, code fences, or extra text.

{
  "invoice_date": "date as written on invoice, empty string if not found",
  "invoice_number": "invoice or tax invoice number, empty string if not found",
  "supplier": "seller or supplier company name, empty string if not found",
  "trn_vat": "TRN or VAT registration number of SUPPLIER — digits only, strip all spaces hyphens dashes and brackets e.g. 100460640400003, empty string if not found",
  "bill_to": "buyer or customer company name, empty string if not found",
  "currency": "3-letter currency code e.g. AED USD EUR",
  "subtotal_excl_vat": "invoice subtotal before VAT — use the label 'Sub Total', 'Subtotal', 'Invoice Subtotal', 'Total Before Discount', or 'Invoice Total Before Discount' as a plain number, empty string if not found",
  "total_discount": "total discount amount for the whole invoice as a plain number — use 'Total Discounted Amount' or 'Total Discount' label; empty string if no discount",
  "vat_amount": "total VAT amount — use label 'VAT', 'VAT 5%', 'Total VAT', 'Total 5% VAT Amount' as plain number, empty string if not found",
  "total_payable": "final grand total including VAT — use label 'Total', 'Total Payable', 'Balance Due', 'Sub Total Inclusive of VAT', 'Amount Due' as plain number, empty string if not found",
  "line_items": [
    {
      "description": "product or service name exactly as written — do NOT include batch number, lot number, V-code, SKU code, or expiry date in the description",
      "sku": "product/vendor code from 'V Code', 'Item Code', 'SKU', 'Product Code', or 'Code' column — empty string if not found; if the same V-code appears on multiple rows, append the row number e.g. '98-17321-00-R5' and '98-17321-00-R8' so each entry is unique",
      "qty": "ONLY from the 'Qty' or 'Quantity' column — the number of units sold — plain number only, no units or text",
      "unit": "unit of measure e.g. PCS KG BOX ML, empty string if not shown",
      "unit_price": "unit price BEFORE discount — use column labelled 'Price Before Discount', 'Rate', 'Unit Price', 'Unit Cost', or 'Price' — plain number only",
      "discount_pct": "discount percentage from 'Discount %' column as plain number e.g. 10.00, empty string if none or zero",
      "discount_amount": "discount money amount per line as plain number, empty string if none",
      "line_total_excl_vat": "line amount EXCLUDING VAT — use column 'Excl.Vat', 'Excl. VAT', 'Taxable Amount', 'Net Amount', or 'Amount' — plain number; must equal qty × (unit_price after discount)",
      "line_vat_amount": "VAT amount for this line — use column 'Vat 5%', 'VAT', 'Tax Amount' — plain number, empty string if not shown per line",
      "line_total": "line total INCLUDING VAT — use column 'Incl.Vat', 'Incl. VAT', 'Total Incl. VAT', 'Gross' — plain number, empty string if not shown"
    }
  ]
}

Rules:
- Extract ALL line items including free/sample items, delivery, and shipping charges.
- CRITICAL — COUNT the rows in the invoice table first, then output exactly that many entries in line_items. Every visible row is a separate line item. Do NOT merge, deduplicate, or skip any row — even if two rows have identical description, V-code, quantity, and price they are separate deliveries and MUST both appear. If the invoice has 8 rows, line_items must have 8 entries.
- CRITICAL — qty: Use ONLY the 'Qty' or 'Quantity' column. NEVER use 'Batch Qty', 'Pack Qty', 'Batch Size', 'Order Qty'. Lot/Batch numbers are NOT quantities. The row serial number (1, 2, 3 … at the far left) is NOT the qty.
- CRITICAL — unit_price: Use 'Price Before Discount', 'Rate', 'Unit Price', or 'Price'. Do NOT use 'Price After Discount', 'Lot No', 'Batch No', or 'Expiry Date' as price.
- CRITICAL — line_total_excl_vat: Use 'Excl.Vat', 'Excl. VAT', or 'Amount' column (before VAT). Do NOT use the 'Incl.Vat' or VAT-inclusive total for this field. line_total_excl_vat = qty × unit_price — it is NEVER equal to unit_price alone unless qty = 1. For multi-line description rows, the Qty column value still appears in the same table row.
- CRITICAL — verify each row: line_total_excl_vat ÷ unit_price should equal qty. If it does not, re-read the Qty column.
- CRITICAL — if invoice shows 'Price Before Discount' AND 'Price After Discount', use 'Price Before Discount' as unit_price and set discount_pct from the Discount % column.
- trn_vat is the SUPPLIER's TRN only — digits only, no spaces or hyphens.
- 'Business Partner TRN', 'Customer TRN', or 'Buyer TRN' on the invoice is the buyer's TRN — do NOT put it in trn_vat.
- total_discount is the invoice-level discount total, not per-line discount.
- Numbers must be plain digits with decimal point — no currency symbols, spaces, or commas.
- Keep product descriptions exactly as printed. Do not add batch/lot/expiry/V-code info to description.
- Use empty string for any field not visible on the invoice.
- Do not guess or invent values."""


def _has_ai_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip())


def extract_purchase_rows_with_openai(content: bytes, ext: str) -> list[dict[str, Any]]:
    import logging as _logging
    _log = _logging.getLogger(__name__)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        parts = openai_purchase_content_parts(content, ext)
        if parts:
            try:
                data = call_claude_invoice_extractor(anthropic_key, parts)
                _log.info("Claude returned keys: %s", list(data.keys()) if isinstance(data, dict) else type(data))
                rows = openai_invoice_to_purchase_rows(data)
                _log.info("Claude extraction yielded %d rows", len(rows))
                if rows:
                    return rows
            except Exception as exc:
                _last_ai_error.set(describe_ai_error(exc))
                _log.warning("Claude extraction failed, trying OpenAI: %s", exc)

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        if not anthropic_key:
            _log.warning("AI extraction skipped: neither ANTHROPIC_API_KEY nor OPENAI_API_KEY is set in environment")
        return []
    parts = openai_purchase_content_parts(content, ext)
    if not parts:
        _log.warning("AI extraction: no content parts generated for ext=%s", ext)
        return []
    parts.append({"type": "text", "text": OPENAI_PURCHASE_EXTRACTION_PROMPT})
    try:
        data = call_openai_invoice_extractor(openai_key, parts)
        _log.info("OpenAI returned keys: %s", list(data.keys()) if isinstance(data, dict) else type(data))
    except Exception as exc:
        _last_ai_error.set(describe_ai_error(exc))
        _log.warning("OpenAI extraction failed: %s", exc)
        return []
    rows = openai_invoice_to_purchase_rows(data)
    _log.info("OpenAI extraction yielded %d rows", len(rows))
    return rows


def openai_purchase_content_parts(content: bytes, ext: str) -> list[dict[str, Any]]:
    ext = (ext or "").lower().lstrip(".")
    if ext in PURCHASE_IMAGE_EXTENSIONS:
        parts = openai_image_parts_with_pillow(content, ext)
        if parts:
            return parts
        # PIL not available or failed — send raw bytes only if within size limit
        if len(content) <= _MAX_IMAGE_BYTES:
            mime = OPENAI_DIRECT_IMAGE_MIME.get(ext)
            if mime:
                return [openai_image_part(content, mime)]
        return []

    if ext == "pdf":
        # Images preserve visual table layout — always prefer them over raw text
        image_parts = openai_pdf_page_image_parts(content)
        if image_parts:
            # Append extracted text as supplemental context alongside the images
            text = extract_pdf_text_with_pdfplumber(content) or extract_pdf_text(content)
            if text and len(text.strip()) > 50:
                image_parts.append({"type": "text", "text": f"Supplemental text extracted from PDF (use images as primary source):\n\n{text[:4000]}"})
            return image_parts
        # Fallback: text-only if pdftoppm is unavailable
        text = extract_pdf_text_with_pdfplumber(content) or extract_pdf_text(content)
        if text and len(text.strip()) > 100:
            return [{"type": "text", "text": f"Invoice text content:\n\n{text}"}]
    return []


def openai_image_part(content: bytes, mime: str = "image/png") -> dict[str, Any]:
    encoded = base64.b64encode(content).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": "high"}}


_MAX_IMAGE_DIMENSION = 1568   # Claude's optimal max edge (≤ 1568px on longest side)
_MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB safety cap (Anthropic limit is 5 MB)


def openai_image_parts_with_pillow(content: bytes, ext: str) -> list[dict[str, Any]]:
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except Exception:
        return []
    try:
        image = Image.open(io.BytesIO(content))
        # Collect all frames (handles multi-frame TIFFs and animated images)
        frames: list[Any] = []
        try:
            while True:
                frames.append(image.copy().convert("RGB"))
                image.seek(image.tell() + 1)
        except EOFError:
            pass
        if not frames:
            frames = [image.convert("RGB")]
        image.close()

        parts: list[dict[str, Any]] = []
        for frame in frames:
            # Downscale if either dimension exceeds the safe maximum
            w, h = frame.size
            if max(w, h) > _MAX_IMAGE_DIMENSION:
                scale = _MAX_IMAGE_DIMENSION / max(w, h)
                frame = frame.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            # Save as JPEG with quality reduction until within size limit
            data = b""
            for quality in (85, 70, 55, 40):
                output = io.BytesIO()
                frame.save(output, format="JPEG", quality=quality, optimize=True)
                data = output.getvalue()
                if len(data) <= _MAX_IMAGE_BYTES:
                    break
            if len(data) > _MAX_IMAGE_BYTES:
                continue  # skip frames that can't be compressed small enough
            parts.append(openai_image_part(data, "image/jpeg"))
        return parts
    except Exception:
        return []


def openai_pdf_page_image_parts(content: bytes) -> list[dict[str, Any]]:
    pdftoppm = find_pdftoppm_executable()
    if not pdftoppm:
        return []
    max_pages = openai_purchase_pdf_max_pages()
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "upload.pdf"
        output_prefix = Path(tmp) / "page"
        pdf_path.write_bytes(content)
        command = [pdftoppm, "-png", "-r", "250", "-f", "1"]
        if max_pages > 0:
            command.extend(["-l", str(max_pages)])
        command.extend([str(pdf_path), str(output_prefix)])
        result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
        if result.returncode != 0:
            return []
        parts: list[dict[str, Any]] = []
        for page_path in sorted(Path(tmp).glob("page-*.png")):
            parts.extend(openai_image_parts_with_pillow(page_path.read_bytes(), "png"))
        return parts


def openai_purchase_pdf_max_pages() -> int:
    try:
        return max(0, int(os.environ.get("OPENAI_PURCHASE_PDF_MAX_PAGES", "10")))
    except ValueError:
        return 10


_CLAUDE_FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-haiku-4-5-20251001")
# Keep one extraction request inside the hosting gateway's time limit (a 504 loses the result):
# shorter per-call timeout, and a rate limit (429) is retried once after a short wait.
_AI_CALL_TIMEOUT = 60
_AI_RATE_LIMIT_RETRIES = 1


def _rate_limit_wait(exc: urllib.error.HTTPError) -> float:
    try:
        return max(1.0, min(10.0, float(exc.headers.get("retry-after") or 5)))
    except (TypeError, ValueError):
        return 5.0


# Why the last AI call in this request failed, in words a user can act on. Extraction falls
# back to text parsing / returns [] on AI errors, which used to hide the real cause (bad key,
# rate limit, timeout) behind "no data found". Set per request (contextvars follow the thread).
_last_ai_error: ContextVar[str] = ContextVar("_last_ai_error", default="")


def describe_ai_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "the AI service rejected the API key — check ANTHROPIC_API_KEY / OPENAI_API_KEY"
        if exc.code == 429:
            return "the AI service is busy (rate limit) — try again in a minute"
        if exc.code >= 500:
            return f"the AI service is temporarily unavailable ({exc.code}) — try again shortly"
        return f"the AI service refused the file ({exc.code}) — it may be too large or unreadable"
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in str(exc).lower():
        return "the AI service timed out — try again, or upload a smaller file"
    if isinstance(exc, urllib.error.URLError):
        return "couldn't reach the AI service — check the server's internet connection"
    if isinstance(exc, ValueError):  # includes json.JSONDecodeError
        return "the AI reply couldn't be read — try again"
    return f"unexpected AI error ({type(exc).__name__})"


def extraction_confidence(
    *, invoice_no_found: bool, party: Any, date: Any, subtotal: Any, vat: Any, total: Any,
    line_count: int, lines_sum: Any = None, check_totals: bool = True,
) -> int:
    """0-100 from what was actually extracted (replaces the old fixed 92/85, which meant the
    "Low confidence" check could never fire). Under 70 = more than one real problem."""
    score = 100
    if not invoice_no_found:
        score -= 30
    if not str(party or "").strip() or str(party).strip().lower() in {"supplier", "customer"}:
        score -= 25
    if not parse_document_date(date):
        score -= 15
    if line_count <= 0:
        score -= 15
    subtotal_d, vat_d, total_d = decimal_value(subtotal), decimal_value(vat), decimal_value(total)
    if total_d <= 0:
        score -= 20
    elif check_totals and abs(subtotal_d + vat_d - total_d) > max(Decimal("0.05"), total_d / 100):
        score -= 20
    if lines_sum is not None and subtotal_d > 0:
        if abs(decimal_value(lines_sum) - subtotal_d) > max(Decimal("0.05"), subtotal_d / 100):
            score -= 10
    return max(0, min(100, score))


def _claude_messages(api_key: str, content: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
    """POST /v1/messages with the configured model; if Anthropic rejects the model itself
    (retired/renamed -> 404, or a 400 naming the model) retry with a current one, so a
    stale model name degrades instead of taking extraction down (as on 2026-08-05)."""
    import logging as _logging
    import time as _time
    configured = os.environ.get("ANTHROPIC_PURCHASE_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"
    models = [configured] + [m for m in _CLAUDE_FALLBACK_MODELS if m != configured]
    last_exc: Exception | None = None
    for model in models:
        payload = {"model": model, "max_tokens": max_tokens, "temperature": 0, "messages": [{"role": "user", "content": content}]}
        for attempt in range(_AI_RATE_LIMIT_RETRIES + 1):
            request = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload).encode("utf-8"),
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=_AI_CALL_TIMEOUT) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < _AI_RATE_LIMIT_RETRIES:
                    _time.sleep(_rate_limit_wait(exc))
                    continue
                body = ""
                try:
                    body = exc.read().decode("utf-8", "ignore")
                except Exception:
                    pass
                if exc.code == 404 or (exc.code == 400 and "model" in body.lower()):
                    _logging.getLogger(__name__).warning("Claude model %s rejected (%s); trying next model", model, exc.code)
                    last_exc = exc
                    break
                raise
    raise last_exc or RuntimeError("No Claude model accepted the request")


def call_claude_invoice_extractor(api_key: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    # Convert OpenAI-style content parts to Anthropic format
    anthropic_content: list[dict[str, Any]] = []
    for part in parts:
        if part.get("type") == "image_url":
            url = part["image_url"]["url"]
            if url.startswith("data:"):
                mime, b64 = url.split(";base64,", 1)
                mime = mime[5:]  # strip "data:"
                anthropic_content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": b64},
                })
        elif part.get("type") == "text":
            anthropic_content.append({"type": "text", "text": part["text"]})
    anthropic_content.append({"type": "text", "text": OPENAI_PURCHASE_EXTRACTION_PROMPT})
    result = _claude_messages(api_key, anthropic_content, 8192)
    raw = str(result["content"][0]["text"] or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def call_openai_invoice_extractor(api_key: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    model = os.environ.get("OPENAI_PURCHASE_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": parts}],
        "max_tokens": 8192,
        "temperature": 0,
    }
    import time as _time
    for attempt in range(_AI_RATE_LIMIT_RETRIES + 1):
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_AI_CALL_TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < _AI_RATE_LIMIT_RETRIES:
                _time.sleep(_rate_limit_wait(exc))
                continue
            raise
    raw = str(result["choices"][0]["message"]["content"] or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


# ── Sales invoice AI extraction ───────────────────────────────────────────────

SALES_INVOICE_EXTRACTION_PROMPT = """You are an invoice data extraction expert.

Read this sales invoice carefully and extract all visible data.
Return ONLY a valid JSON object. Do not include markdown, code fences, or extra text.

{
  "invoice_date": "date as written on invoice, empty string if not found",
  "invoice_number": "invoice or tax invoice number, empty string if not found",
  "seller": "the company that issued this invoice (seller/issuer), empty string if not found",
  "trn_vat": "TRN or VAT registration number of the SELLER — digits only, strip all spaces hyphens and brackets, empty string if not found",
  "customer": "buyer or customer company name, empty string if not found",
  "customer_trn": "TRN or VAT number of the buyer/customer — digits only, empty string if not found",
  "currency": "3-letter currency code e.g. AED USD EUR",
  "subtotal_excl_vat": "invoice subtotal before VAT as a plain number, empty string if not found",
  "vat_amount": "total VAT amount as a plain number, empty string if not found",
  "total_payable": "final grand total including VAT as a plain number, empty string if not found",
  "payment_terms": "payment terms e.g. '30 days' 'Net 30' 'Due on receipt', empty string if not found",
  "due_date": "payment due date as written, empty string if not found",
  "line_items": [
    {
      "description": "product or service name exactly as written",
      "qty": "quantity as a plain number",
      "unit": "unit of measure e.g. PCS KG BOX ML, empty string if not shown",
      "unit_price": "unit price before VAT as a plain number",
      "line_total_excl_vat": "line amount excluding VAT as a plain number",
      "vat_amount": "VAT amount for this line as a plain number, empty string if not shown per line",
      "line_total": "line total including VAT as a plain number, empty string if not shown"
    }
  ]
}

Rules:
- Numbers must be plain digits with decimal point — no currency symbols, spaces, or commas.
- Extract ALL line items.
- Use empty string for any field not visible on the invoice.
- Do not guess or invent values."""


def _sfloat(val: Any) -> float:
    try:
        return float(str(val or "").replace(",", "").strip() or "0")
    except (ValueError, TypeError):
        return 0.0


def ingest_sales_invoice_document(db: Session, principal: Principal, file: dict[str, Any]) -> list[dict[str, Any]]:
    name = str(file.get("name") or "sales-upload").strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    content = decode_uploaded_file(file)
    if not content:
        return [{"extraction_error": True, "error_message": "Uploaded file content was empty", "sourceFile": name}]
    try:
        if ext in PURCHASE_IMAGE_EXTENSIONS or ext == "pdf":
            if not _has_ai_key():
                return [{"extraction_error": True, "error_message": "AI extraction not configured — add ANTHROPIC_API_KEY or OPENAI_API_KEY to your .env file to enable PDF/image reading", "sourceFile": name}]
            _last_ai_error.set("")
            result = _extract_sales_with_ai(content, ext, name)
            if result:
                return result
            reason = _last_ai_error.get()
            message = f"AI reading failed: {reason}" if reason else "Could not extract data from image/PDF. Try a clearer file or CSV/Excel."
            return [{"extraction_error": True, "error_message": message, "sourceFile": name}]
        elif ext == "csv":
            return _parse_sales_csv(content, name)
        elif ext in {"xlsx", "xlsm"}:
            return _parse_sales_xlsx(content, name)
        else:
            return [{"extraction_error": True, "error_message": f"Unsupported format: .{ext or 'unknown'}. Use PDF, image, CSV, or Excel.", "sourceFile": name}]
    except Exception as exc:
        return [{"extraction_error": True, "error_message": f"Could not parse file: {exc}", "sourceFile": name}]


def _extract_sales_with_ai(content: bytes, ext: str, name: str) -> list[dict[str, Any]]:
    parts = openai_purchase_content_parts(content, ext)
    if not parts:
        return []
    data: dict[str, Any] = {}
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        try:
            anthropic_content: list[dict[str, Any]] = []
            for part in parts:
                if part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if url.startswith("data:"):
                        mime, b64 = url.split(";base64,", 1)
                        anthropic_content.append({"type": "image", "source": {"type": "base64", "media_type": mime[5:], "data": b64}})
                elif part.get("type") == "text":
                    anthropic_content.append({"type": "text", "text": part["text"]})
            anthropic_content.append({"type": "text", "text": SALES_INVOICE_EXTRACTION_PROMPT})
            result = _claude_messages(anthropic_key, anthropic_content, 4096)
            raw = re.sub(r"^```(?:json)?\s*", "", str(result["content"][0]["text"] or "").strip(), flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            if not isinstance(data, dict):
                data = {}
        except Exception as exc:
            _last_ai_error.set(describe_ai_error(exc))
            import logging
            logging.getLogger(__name__).warning("Claude sales extraction failed, trying OpenAI: %s", exc)
    if not data:
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_key:
            try:
                data = call_openai_invoice_extractor(openai_key, parts + [{"type": "text", "text": SALES_INVOICE_EXTRACTION_PROMPT}])
            except Exception as exc:
                _last_ai_error.set(describe_ai_error(exc))
                import logging
                logging.getLogger(__name__).warning("OpenAI sales extraction failed: %s", exc)
    if not data:
        return []
    return [_ai_to_sales_invoice(data, name)]


def _ai_to_sales_invoice(data: dict[str, Any], source_file: str) -> dict[str, Any]:
    raw_lines = data.get("line_items") or []
    lines = []
    for item in raw_lines:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("description") or "").strip()
        if not desc:
            continue
        qty = _sfloat(item.get("qty") or 1)
        unit_price = _sfloat(item.get("unit_price"))
        line_excl = _sfloat(item.get("line_total_excl_vat") or item.get("line_total")) or round(qty * unit_price, 2)
        if not unit_price and line_excl:
            unit_price = round(line_excl / (qty or 1), 4)  # the server re-totals from qty x unit price
        lines.append({
            "description": desc,
            "qty": qty,
            "unit": str(item.get("unit") or "PCS").strip(),
            "unit_price": unit_price,
            "total": line_excl,
            "vat": _sfloat(item.get("vat_amount")),
        })
    subtotal = _sfloat(data.get("subtotal_excl_vat")) or round(sum(ln["total"] for ln in lines), 2)
    vat_amount = _sfloat(data.get("vat_amount")) or round(sum(ln["vat"] for ln in lines), 2)
    total = _sfloat(data.get("total_payable")) or round(subtotal + vat_amount, 2)
    invoice_no_found = bool(str(data.get("invoice_number") or data.get("invoice_no") or "").strip())
    invoice_no = str(data.get("invoice_number") or data.get("invoice_no") or "").strip() or f"AI-{source_file[:12].upper()}"
    confidence = extraction_confidence(
        invoice_no_found=invoice_no_found, party=data.get("customer") or data.get("bill_to"),
        date=data.get("invoice_date"), subtotal=subtotal, vat=vat_amount, total=total,
        line_count=len(lines), lines_sum=sum(ln["total"] for ln in lines) if lines else None,
    )
    return {
        "invoice_no": invoice_no,
        "customer": str(data.get("customer") or data.get("bill_to") or "").strip(),
        "date": normalize_document_date(str(data.get("invoice_date") or "").strip()),
        "due_date": normalize_document_date(str(data.get("due_date") or data.get("payment_terms") or "30 days").strip()),
        "subtotal": round(subtotal, 2),
        "vat_amount": round(vat_amount, 2),
        "vat": round(vat_amount, 2),
        "total": round(total, 2),
        "seller": str(data.get("seller") or "").strip(),
        "trn": str(data.get("trn_vat") or "").strip(),
        "customer_trn": str(data.get("customer_trn") or "").strip(),
        "currency": str(data.get("currency") or "AED").strip(),
        "lines": lines,
        "status": "Draft",
        "source": "AI Upload",
        "sourceFile": source_file,
        "confidence": confidence,
    }


def _parse_sales_csv(content: bytes, name: str) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    except Exception as exc:
        return [{"extraction_error": True, "error_message": f"Could not read CSV: {exc}", "sourceFile": name}]
    if not rows:
        return [{"extraction_error": True, "error_message": "CSV file is empty.", "sourceFile": name}]

    def _col(row: dict[str, str], *keys: str) -> str:
        lk = {k.strip().lower(): v for k, v in row.items()}
        for key in keys:
            if key.lower() in lk:
                return str(lk[key.lower()] or "").strip()
        return ""

    invoices: dict[str, dict[str, Any]] = {}
    for row in rows:
        inv_no = _col(row, "invoice_no", "invoice_number", "invoice number", "inv_no", "ref", "reference")
        if not inv_no:
            continue
        if inv_no not in invoices:
            invoices[inv_no] = {
                "invoice_no": inv_no,
                "customer": _col(row, "customer", "customer_name", "client", "bill_to"),
                "date": normalize_document_date(_col(row, "date", "invoice_date", "issue_date")),
                "due_date": normalize_document_date(_col(row, "due_date", "payment_due", "due")),
                "subtotal": 0.0, "vat_amount": 0.0, "vat": 0.0, "total": 0.0,
                "lines": [],
                "status": _col(row, "status") or "Draft",
                "source": "CSV Upload",
                "sourceFile": name,
            }
        desc = _col(row, "description", "item", "product", "service")
        if desc:
            qty = _sfloat(_col(row, "qty", "quantity")) or 1.0
            unit_price = _sfloat(_col(row, "unit_price", "price", "rate", "unit price"))
            line_total = _sfloat(_col(row, "total", "line_total", "amount")) or round(qty * unit_price, 2)
            if not unit_price and line_total:
                unit_price = round(line_total / qty, 4)
            line_vat = _sfloat(_col(row, "vat", "vat_amount", "tax"))
            invoices[inv_no]["lines"].append({
                "description": desc,
                "qty": qty,
                "unit": _col(row, "unit", "uom") or "PCS",
                "unit_price": unit_price,
                "total": line_total,
                "vat": line_vat,
            })
    result = []
    for inv in invoices.values():
        subtotal = round(sum(ln["total"] for ln in inv["lines"]), 2)
        vat_amt = round(sum(ln["vat"] for ln in inv["lines"]), 2)
        inv.update(subtotal=subtotal, vat_amount=vat_amt, vat=vat_amt, total=round(subtotal + vat_amt, 2))
        result.append(inv)
    if not result:
        return [{"extraction_error": True, "error_message": "No invoice rows found. Ensure columns 'invoice_no' and 'description' exist.", "sourceFile": name}]
    return result


def _parse_sales_xlsx(content: bytes, name: str) -> list[dict[str, Any]]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as workbook:
            if sum(info.file_size for info in workbook.infolist()) > _XLSX_MAX_UNCOMPRESSED_SIZE:
                raise ValueError("This file is too large or unusually compressed to process")
            shared_strings = read_xlsx_shared_strings(workbook)
            sheet_xmls = [workbook.read(s) for s in xlsx_sheet_names(workbook)]
        table_rows: list[list[str]] = []
        for sheet_xml in sheet_xmls:
            root = ElementTree.fromstring(sheet_xml)
            ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for row_el in root.findall(".//x:sheetData/x:row", ns):
                cells: list[str] = []
                expected_idx = 0
                for cell in row_el.findall("x:c", ns):
                    ref = str(cell.attrib.get("r") or "")
                    col_idx = xlsx_column_index("".join(ch for ch in ref if ch.isalpha()))
                    while expected_idx < col_idx:
                        cells.append("")
                        expected_idx += 1
                    cells.append(read_xlsx_cell(cell, shared_strings, ns))
                    expected_idx += 1
                if any(v.strip() for v in cells):
                    table_rows.append(cells)
        if not table_rows:
            return [{"extraction_error": True, "error_message": "Excel file appears empty.", "sourceFile": name}]
        headers = [str(h or "").strip().lower() for h in table_rows[0]]
        dict_rows: list[dict[str, str]] = []
        for raw in table_rows[1:]:
            row = {headers[i]: (raw[i] if i < len(raw) else "") for i in range(len(headers)) if headers[i]}
            if any(str(v or "").strip() for v in row.values()):
                dict_rows.append(row)
        if not dict_rows:
            return [{"extraction_error": True, "error_message": "No data rows in Excel file.", "sourceFile": name}]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(dict_rows[0].keys()))
        writer.writeheader()
        writer.writerows(dict_rows)
        return _parse_sales_csv(buf.getvalue().encode("utf-8"), name)
    except Exception as exc:
        return [{"extraction_error": True, "error_message": f"Could not read Excel file: {exc}", "sourceFile": name}]


_JUNK_PRODUCT_RE = re.compile(
    r"^(?:"
    r"(?:AED|USD|EUR|GBP|SAR|QAR|BHD|KWD|OMR)\s*[\d,]+(?:\.\d+)?"  # currency amount e.g. AED1, AED 100.00
    r"|[\d,]+(?:\.\d+)?\s*(?:AED|USD|EUR|GBP|SAR|QAR|BHD|KWD|OMR)"  # reversed e.g. 100 AED
    r"|[\d,]+(?:\.\d+)?%?"  # pure number or percentage
    r"|(?:sub\s*total|grand\s*total|net\s*total|total\s*excl|total\s*incl|vat\s*total|tax\s*total|amount\s*due|balance\s*due|total\s*payable|total\s*amount|total\s*due)"
    r"|(?:vat|tax|discount|shipping|delivery|freight|handling|charges?|fees?|s&h)"
    r")$",
    re.IGNORECASE,
)


def _is_junk_product_description(desc: str) -> bool:
    return bool(_JUNK_PRODUCT_RE.match(desc.strip()))


def openai_invoice_to_purchase_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = data.get("line_items") if isinstance(data.get("line_items"), list) else []
    if not items:
        return []

    subtotal = decimal_value(data.get("subtotal_excl_vat"))
    vat_total = decimal_value(data.get("vat_amount"))
    total_payable = decimal_value(data.get("total_payable"))
    total_discount = decimal_value(data.get("total_discount"))
    discount_applies_to_subtotal = openai_summary_discount_applies(subtotal, total_discount, vat_total, total_payable)
    line_nets = openai_line_net_amounts(items, subtotal, vat_total, total_payable)
    if not any(line_nets):
        fallback_subtotal = subtotal or max(Decimal("0"), total_payable - vat_total)
        if fallback_subtotal:
            line_nets = distribute_invoice_amount(fallback_subtotal, len(items))
    net_sum = sum(line_nets, Decimal("0"))
    rows: list[dict[str, Any]] = []

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        product = str(item.get("description") or "").strip()
        if not product:
            continue
        line_net = line_nets[index] if index < len(line_nets) else Decimal("0")
        raw_qty = decimal_value(item.get("qty")) or Decimal("1")
        raw_unit_price = decimal_value(first_present_raw(item, "unit_price", "rate", "price", "unit_cost"))
        # Correct qty when unit_price × qty doesn't match the (cross-validated) line_net.
        # Handles the case where the AI reads the serial number or defaults to 1 instead
        # of the actual Qty column value (common on invoices with multi-line descriptions).
        if raw_unit_price and line_net and raw_unit_price > Decimal("0"):
            expected_net = raw_unit_price * raw_qty
            if abs(expected_net - line_net) > max(line_net * Decimal("0.03"), Decimal("0.50")):
                computed_qty = line_net / raw_unit_price
                rounded_qty = int(round(float(computed_qty)))
                if rounded_qty >= 1 and abs(Decimal(str(rounded_qty)) - computed_qty) < Decimal("0.06"):
                    raw_qty = Decimal(str(rounded_qty))
        # Use per-line VAT from AI if available, otherwise distribute invoice VAT proportionally
        line_vat_explicit = decimal_value(item.get("line_vat_amount"))
        if line_vat_explicit:
            vat = line_vat_explicit
        elif vat_total and net_sum:
            vat = vat_total * (line_net / net_sum)
        elif vat_total and index == 0:
            vat = vat_total
        else:
            vat = Decimal("0")
        # Pass corrected item so openai_unit_price uses the right qty
        item_corrected = dict(item)
        item_corrected["qty"] = str(raw_qty)
        unit_cost = openai_unit_price(item_corrected, line_net)
        rows.append(normalize_purchase_row({
            "invoice_no": data.get("invoice_number") or "",
            "date": data.get("invoice_date") or "",
            "supplier": data.get("supplier") or "",
            "supplier_trn": data.get("trn_vat") or "",
            "bill_to": data.get("bill_to") or "",
            "currency": data.get("currency") or "AED",
            "product": product,
            "sku": str(item.get("sku") or "").strip(),
            "quantity": raw_qty,
            "unit": item.get("unit") or "PCS",
            "unit_cost": unit_cost,
            "unit_price": unit_cost,
            "cost": unit_cost,
            "discount": item.get("discount_pct") or "",
            "discount_amount": item.get("discount_amount") or "",
            "discount_type": "Fixed" if discount_applies_to_subtotal else "None",
            "discount_value": data.get("total_discount") if discount_applies_to_subtotal else "",
            "vat_amount": vat,
            "line_total": line_net,
            "amount": line_net,
            "net_amount": line_net,
            "subtotal": line_net,
            "tax_type": "VAT 5%" if vat_total else "None",
            "raw": item,
        }))
    return rows


def openai_summary_discount_applies(
    subtotal: Decimal,
    total_discount: Decimal,
    vat_total: Decimal,
    total_payable: Decimal,
) -> bool:
    if not subtotal or not total_discount:
        return False
    if not total_payable:
        return True
    taxable_from_total = max(Decimal("0"), total_payable - vat_total)
    return abs((subtotal - total_discount) - taxable_from_total) <= Decimal("0.05")


def openai_line_net_amounts(
    items: list[Any],
    subtotal: Decimal,
    vat_total: Decimal,
    total_payable: Decimal,
) -> list[Decimal]:
    # Priority 1: explicit pre-VAT amounts (line_total_excl_vat etc.)
    explicit_nets = [openai_explicit_line_net_amount(item) if isinstance(item, dict) else Decimal("0") for item in items]
    if any(explicit_nets):
        return explicit_nets

    # Priority 2: gross line_total values from AI
    gross_values = [openai_line_gross_amount(item) if isinstance(item, dict) else Decimal("0") for item in items]
    gross_sum = sum(gross_values, Decimal("0"))
    if gross_sum:
        target_net = subtotal
        if not target_net and total_payable and vat_total:
            target_net = max(Decimal("0"), total_payable - vat_total)
        if target_net:
            # If the AI's line_total values already sum to ≈ the pre-VAT subtotal,
            # they are pre-VAT amounts — use them directly (common on UAE/GCC invoices).
            tolerance = max(target_net * Decimal("0.02"), Decimal("0.50"))
            if abs(gross_sum - target_net) <= tolerance:
                return gross_values
            return distribute_by_weight(target_net, gross_values)

        # No subtotal available — if no VAT, line_totals are the net amounts
        if not vat_total:
            return gross_values
        # VAT present but no subtotal: back-calculate per line (risky — only last resort)
        return [max(Decimal("0"), gross - (vat_total * (gross / gross_sum))) for gross in gross_values]

    # Priority 3: calculate from unit_price × qty
    calculated = [openai_line_net_from_unit_price(item) if isinstance(item, dict) else Decimal("0") for item in items]
    return calculated


def openai_explicit_line_net_amount(item: dict[str, Any]) -> Decimal:
    explicit = first_decimal_value(
        item,
        "line_total_excl_vat",
        "line_total_before_vat",
        "line_total_before_tax",
        "line_subtotal",
        "subtotal_excl_vat",
        "taxable_amount",
        "taxable_value",
        "net_amount",
        "net_value",
        "amount_excl_vat",
        "amount_before_tax",
    )
    # Cross-check: use Incl.VAT − line_vat when explicit is absent or inconsistent.
    # Catches the common AI mistake of putting unit_price in line_total_excl_vat
    # instead of qty × unit_price (the actual Excl.Vat column value).
    line_vat = first_decimal_value(item, "line_vat_amount")
    line_gross = first_decimal_value(item, "line_total", "line_total_incl_vat", "line_total_including_vat")
    if line_vat and line_gross:
        net_from_gross = line_gross - line_vat
        if net_from_gross > Decimal("0"):
            if not explicit:
                return net_from_gross
            tolerance = max(Decimal("0.10"), line_gross * Decimal("0.01"))
            if abs(explicit + line_vat - line_gross) > tolerance:
                return net_from_gross
    return explicit


def openai_line_gross_amount(item: dict[str, Any]) -> Decimal:
    return first_decimal_value(
        item,
        "line_total",
        "line_total_incl_vat",
        "line_total_including_vat",
        "total_incl_vat",
        "amount_incl_vat",
        "gross_amount",
        "gross_value",
        "total",
        "amount",
    )


def openai_line_net_amount(item: dict[str, Any]) -> Decimal:
    explicit = openai_explicit_line_net_amount(item)
    if explicit:
        return explicit
    return openai_line_net_from_unit_price(item) or openai_line_gross_amount(item)


def openai_line_net_from_unit_price(item: dict[str, Any]) -> Decimal:
    qty = first_decimal_value(item, "qty", "quantity") or Decimal("1")
    unit_price = first_decimal_value(item, "unit_price", "rate", "price", "unit_cost")
    discount = first_decimal_value(item, "discount_amount", "discount_value", "discount")
    if unit_price:
        return max(Decimal("0"), (qty * unit_price) - discount)
    return Decimal("0")


def openai_unit_price(item: dict[str, Any], line_net: Decimal) -> Any:
    raw = str(first_present_raw(item, "unit_price", "rate", "price", "unit_cost") or "").strip()
    if raw.lower() == "free":
        return 0
    value = decimal_value(raw)
    qty = first_decimal_value(item, "qty", "quantity") or Decimal("1")
    if value:
        # If unit_price × qty exceeds the computed line net by more than 10%, the AI
        # has placed a summary total (e.g. invoice grand total) in the unit_price field.
        # Fall back to line_net / qty in that case.
        if line_net and value * qty > line_net * Decimal("1.1"):
            return line_net / qty if qty else line_net
        return value
    return line_net / qty if qty else line_net


def first_present_raw(row: dict[str, Any], *keys: str) -> Any:
    normalized = {normalize_header(key): value for key, value in row.items()}
    for key in keys:
        value = normalized.get(normalize_header(key))
        if value not in (None, ""):
            return value
    return ""


def first_decimal_value(row: dict[str, Any], *keys: str) -> Decimal:
    for key in keys:
        value = first_present_raw(row, key)
        if value not in (None, ""):
            parsed = decimal_value(value)
            if parsed:
                return parsed
    return Decimal("0")


def distribute_invoice_amount(amount: Decimal, count: int) -> list[Decimal]:
    if count <= 0:
        return []
    share = (amount / Decimal(count)).quantize(Decimal("0.01"))
    values = [share for _ in range(count)]
    values[-1] = amount - sum(values[:-1], Decimal("0"))
    return values


def distribute_by_weight(amount: Decimal, weights: list[Decimal]) -> list[Decimal]:
    total_weight = sum(weights, Decimal("0"))
    if amount <= 0 or total_weight <= 0:
        return [Decimal("0") for _ in weights]
    values = [(amount * (weight / total_weight)).quantize(Decimal("0.01")) for weight in weights]
    if values:
        values[-1] = amount - sum(values[:-1], Decimal("0"))
    return values


def extract_image_text_with_tesseract(content: bytes, ext: str) -> str:
    tesseract = find_tesseract_executable()
    if not tesseract:
        return ""
    suffix = "." + ("jpg" if ext == "jpeg" else ext)
    with tempfile.TemporaryDirectory() as tmp:
        output_base = Path(tmp) / "ocr"
        variants = [content]
        processed = preprocess_image_for_ocr(content)
        if processed and processed != content:
            variants.append(processed)
        best_text = ""
        best_score = -1
        for index, variant in enumerate(variants):
            image_path = Path(tmp) / f"upload_{index}{'.png' if index else suffix}"
            image_path.write_bytes(variant)
            text = run_tesseract_image(tesseract, image_path, output_base)
            score = score_invoice_ocr_text(text)
            if score > best_score:
                best_text = text
                best_score = score
        return best_text


def run_tesseract_image(tesseract: str, image_path: Path, output_base: Path) -> str:
    for psm in ("6", "4"):
        result = subprocess.run(
            [tesseract, str(image_path), str(output_base), "--psm", psm, "-c", "preserve_interword_spaces=1"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if result.returncode == 0:
            try:
                return output_base.with_suffix(".txt").read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return ""
    return ""


def score_invoice_ocr_text(text: str) -> int:
    if not text:
        return 0
    item_codes = len(re.findall(r"[\[\{\(1].{0,3}[0-9]{4,}[\]\}\)]", text))
    money_values = len(money_values_in_line(text))
    product_hints = len(re.findall(r"\b(description|qty|invoice|vat|total|discount)\b", text, flags=re.IGNORECASE))
    return item_codes * 20 + money_values * 3 + product_hints


def preprocess_image_for_ocr(content: bytes) -> bytes | None:
    try:
        from PIL import Image, ImageOps, ImageFilter  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        with Image.open(io.BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image).convert("L")
            width, height = image.size
            scale = 2 if max(width, height) < 2400 else 1
            if scale > 1:
                image = image.resize((width * scale, height * scale))
            image = ImageOps.autocontrast(image)
            image = image.filter(ImageFilter.SHARPEN)
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
    except Exception:
        return None


def find_tesseract_executable() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def find_pdftoppm_executable() -> str | None:
    found = shutil.which("pdftoppm")
    if found:
        return found
    candidates = [
        r"C:\Program Files\poppler\Library\bin\pdftoppm.exe",
        r"C:\Program Files\poppler\bin\pdftoppm.exe",
    ]
    # WinGet installs Poppler under a versioned subfolder — scan for any version
    winget_base = Path(r"C:\Users") / (os.environ.get("USERNAME") or "")
    winget_poppler = winget_base / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if winget_poppler.exists():
        for pkg in winget_poppler.glob("oschwartz10612.Poppler_*"):
            candidate = next(pkg.glob("**/pdftoppm.exe"), None)
            if candidate:
                return str(candidate)
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def extract_pdf_text_with_ocr(content: bytes) -> str:
    pdftoppm = find_pdftoppm_executable()
    tesseract = find_tesseract_executable()
    if not pdftoppm or not tesseract:
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "upload.pdf"
        output_prefix = Path(tmp) / "page"
        pdf_path.write_bytes(content)
        result = subprocess.run(
            [pdftoppm, "-png", "-r", "200", "-f", "1", "-l", "3", str(pdf_path), str(output_prefix)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            return ""
        chunks: list[str] = []
        for image_path in sorted(Path(tmp).glob("page-*.png")):
            ocr_base = image_path.with_suffix("")
            result = subprocess.run(
                [tesseract, str(image_path), str(ocr_base), "--psm", "6", "-c", "preserve_interword_spaces=1"],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            if result.returncode == 0:
                try:
                    chunks.append(ocr_base.with_suffix(".txt").read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
        return "\n".join(chunks).strip()


_PDF_IMAGE_STREAM = re.compile(rb"/Subtype\s*/Image|/(DCTDecode|JPXDecode|CCITTFaxDecode|JBIG2Decode)")


def extract_pdf_text(content: bytes) -> str:
    chunks: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", content, flags=re.DOTALL):
        stream = match.group(1)
        header = content[max(0, match.start() - 300):match.start()]
        # Image streams hold no text; scanning their bytes with the TJ regexes below took
        # minutes per page on scanned PDFs (gateway 504s on bulk uploads).
        if _PDF_IMAGE_STREAM.search(header):
            continue
        if b"FlateDecode" in header:
            try:
                stream = zlib.decompress(stream)
            except Exception:
                continue
        text = extract_pdf_text_from_stream(stream)
        if text:
            chunks.append(text)
    if not chunks:
        chunks.append(extract_pdf_literal_text(content))
    return "\n".join(chunks).strip()


def extract_pdf_text_from_stream(stream: bytes) -> str:
    data = stream.decode("latin-1", errors="ignore")
    values: list[str] = []
    for array in re.findall(r"\[([^\[\]]*)\]\s*TJ", data):
        values.extend(decode_pdf_string(value) for value in re.findall(r"\((?:\\.|[^\\()])*\)", array))
        values.append("\n")
    for value in re.findall(r"(\((?:\\.|[^\\()])*\))\s*Tj", data):
        values.append(decode_pdf_string(value))
        values.append("\n")
    for value in re.findall(r"<([0-9A-Fa-f\s]+)>\s*Tj", data):
        try:
            raw = bytes.fromhex(re.sub(r"\s+", "", value))
            values.append(raw.decode("utf-16-be", errors="ignore") or raw.decode("latin-1", errors="ignore"))
            values.append("\n")
        except ValueError:
            continue
    return " ".join(part for part in values if part).strip()


def extract_pdf_literal_text(content: bytes) -> str:
    data = content.decode("latin-1", errors="ignore")
    values = [decode_pdf_string(value) for value in re.findall(r"\((?:\\.|[^\\()])*\)", data)]
    return " ".join(value for value in values if meaningful_document_token(value))


def decode_pdf_string(value: str) -> str:
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    replacements = {"n": "\n", "r": "\n", "t": "\t", "b": "", "f": "", "\\": "\\", "(": "(", ")": ")"}
    def replace_escape(match: re.Match[str]) -> str:
        token = match.group(1)
        if token.isdigit():
            try:
                return chr(int(token[:3], 8))
            except ValueError:
                return ""
        return replacements.get(token, token)
    return re.sub(r"\\([0-7]{1,3}|.)", replace_escape, value).strip()


def meaningful_document_token(value: str) -> bool:
    text = str(value or "").strip()
    return len(text) > 1 and any(ch.isalpha() for ch in text)


def purchase_rows_from_document_text(text: str) -> list[dict[str, Any]]:
    lines = normalize_document_lines(text)
    lines = [line for line in lines if line]
    if not lines:
        lines = [re.sub(r"\s+", " ", text).strip()]
    invoice_no = find_document_value(lines, (
        r"\b(INV\/[0-9]{4}\/[0-9]{2,})\b",
        r"invoice\s*(?:no|number|#|num)[:\s-]*([A-Z0-9][A-Z0-9\-\/]{2,})",
        r"inv\s*(?:no|#)[:\s-]*([A-Z0-9][A-Z0-9\-\/]{2,})",
        r"bill\s*(?:no|number|#)?[:\s-]*([A-Z0-9][A-Z0-9\-\/]{2,})",
        r"(?:tax\s*)?invoice[:\s-]+([A-Z0-9][A-Z0-9\-\/]{2,})",
    ))
    date = find_document_value(lines, (
        r"(?:invoice|document)?\s*date[:\s-]*([0-9]{1,2}[\/\-.][0-9]{1,2}[\/\-.][0-9]{2,4})",
        r"date[:\s-]*([0-9]{4}[\/\-.][0-9]{1,2}[\/\-.][0-9]{1,2})",
    ))
    if not date:
        date = find_invoice_date_near_number(lines, invoice_no)
    supplier = find_supplier_name(lines)
    trn = find_document_value(lines, (r"\bTRN[:\s-]*([0-9]{10,20})", r"tax\s+registration\s+(?:number|no)[:\s-]*([0-9]{10,20})"))
    total = find_money_after_label(lines, ("sub total inclusive", "grand total", "invoice total", "total amount", "net payable", "amount due", "total"))
    vat = find_money_after_label(lines, ("total 5% vat amount", "vat amount", "tax amount", "vat", "tax"))
    subtotal = find_money_after_label(lines, ("invoice subtotal", "total before discount", "subtotal", "sub total", "taxable amount", "taxable value", "net amount"))
    pay_term = find_document_value(lines, (r"payment\s*terms?[:\s-]*([0-9]+\s*days?)", r"\b([0-9]+\s*days?)\b"))
    item_rows = purchase_item_rows_from_lines(lines, invoice_no, date, supplier, trn, pay_term)
    if item_rows:
        return item_rows
    if not invoice_no and not supplier and not total:
        return []
    return [normalize_purchase_row({
        "invoice_no": invoice_no or "PDF-INVOICE",
        "date": date,
        "supplier": supplier or "Supplier",
        "supplier_trn": trn,
        "pay_term": pay_term,
        "product": "Extracted purchase invoice",
        "quantity": 1,
        "unit": "PCS",
        "unit_cost": subtotal or total,
        "vat_amount": vat,
        "line_total": subtotal or max(Decimal("0"), total - vat),
        "tax_type": "VAT 5%" if vat else "None",
    })]


def normalize_document_lines(text: str) -> list[str]:
    text = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", text or "")
    text = re.sub(r"(?i)(invoice\s*(?:no|number|#)|bill\s*(?:no|number|#)|date|supplier|vendor|seller|trn|subtotal|sub\s+total|vat|tax\s+amount|grand\s+total|amount\s+due|description|product|item|qty|quantity|unit\s+price|rate|amount)", r"\n\1", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) > 1:
        return lines
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return []
    labels = (
        "invoice no",
        "invoice number",
        "bill no",
        "date",
        "supplier",
        "vendor",
        "seller",
        "trn",
        "subtotal",
        "sub total",
        "vat",
        "tax amount",
        "total",
        "amount due",
        "description",
        "product",
        "item",
    )
    pattern = r"\s+(?=(?:" + "|".join(re.escape(label) for label in labels) + r")\b)"
    return [line.strip(" :-") for line in re.split(pattern, compact, flags=re.IGNORECASE) if line and line.strip(" :-")]


def find_document_value(lines: list[str], patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        for line in lines[:80]:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return ""


def find_invoice_date_near_number(lines: list[str], invoice_no: str) -> str:
    date_pattern = r"[0-9]{1,2}[\/\-.][0-9]{1,2}[\/\-.][0-9]{2,4}"
    if invoice_no:
        for index, line in enumerate(lines[:80]):
            if invoice_no in line:
                dates = re.findall(date_pattern, " ".join(lines[index:index + 3]))
                if dates:
                    return dates[0]
    for index, line in enumerate(lines[:80]):
        if re.search(r"\b(document|invoice)\s+date\b", line, flags=re.IGNORECASE):
            dates = re.findall(date_pattern, " ".join(lines[index:index + 3]))
            if dates:
                return dates[0]
    return ""


def find_supplier_name(lines: list[str]) -> str:
    for line in lines[:30]:
        match = re.search(r"^(?:supplier|vendor|seller)(?:\s+name)?[:\s-]+(.+)", line, flags=re.IGNORECASE)
        if match:
            return clean_document_label_value(match.group(1))
    for line in lines[:15]:
        if re.search(r"\b(L\.?L\.?C\.?|LLC|LTD|LIMITED|FZE|FZC)\b", line, flags=re.IGNORECASE):
            return clean_document_label_value(line)[:120]
    for line in lines[:8]:
        tax_invoice = re.search(r"\btax\s+invoice\b", line, flags=re.IGNORECASE)
        if tax_invoice:
            prefix = clean_document_label_value(line[:tax_invoice.start()])
            if prefix and any(ch.isalpha() for ch in prefix):
                return prefix[:120]
        if (
            not re.search(r"\b(invoice|tax|vat|trn|date|total|bill|description|qty|quantity|amount|rate|price)\b", line, flags=re.IGNORECASE)
            and not re.search(r"\bINV\/[0-9]{4}\/[0-9]{2,}\b", line, flags=re.IGNORECASE)
            and not re.search(r"[0-9][0-9,]*\.[0-9]{2}", line)
            and any(ch.isalpha() for ch in line)
        ):
            return line[:120]
    return ""


def clean_document_label_value(value: str) -> str:
    cleaned = re.split(
        r"\b(invoice\s*(?:no|number)?|bill\s*(?:no|number)?|date|trn|tax\s+registration|subtotal|sub\s+total|vat|tax\s+amount|total|amount\s+due)\b",
        str(value or ""),
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return re.sub(r"\s+", " ", cleaned).strip(" :-")


def find_money_after_label(lines: list[str], labels: tuple[str, ...]) -> Decimal:
    for label in labels:
        for line in reversed(lines):
            if re.search(rf"\b{re.escape(label)}\b", line, flags=re.IGNORECASE):
                money = money_values_in_line(line)
                if money:
                    return decimal_value(money[-1])
    return Decimal("0")


def purchase_columnar_item_rows_from_lines(
    lines: list[str],
    invoice_no: str,
    date: str,
    supplier: str,
    trn: str,
    pay_term: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: list[str] = []
    for line in lines:
        normalized_line = normalize_ocr_item_line(line)
        if is_document_summary_line(normalized_line):
            if current:
                row = parse_columnar_purchase_item(current, invoice_no, date, supplier, trn, pay_term)
                if row:
                    rows.append(row)
                current = []
            continue
        if is_columnar_purchase_item_start(normalized_line):
            if current:
                row = parse_columnar_purchase_item(current, invoice_no, date, supplier, trn, pay_term)
                if row:
                    rows.append(row)
            current = [normalized_line]
            continue
        if current:
            current.append(normalized_line)
            if len(money_values_in_line(" ".join(current))) >= 5:
                row = parse_columnar_purchase_item(current, invoice_no, date, supplier, trn, pay_term)
                if row:
                    rows.append(row)
                    current = []
    if current:
        row = parse_columnar_purchase_item(current, invoice_no, date, supplier, trn, pay_term)
        if row:
            rows.append(row)
    return rows[:200]


def normalize_ocr_item_line(line: str) -> str:
    text = str(line or "")
    text = text.replace("Â£", "E").replace("£", "E").replace("€", "E").replace("â‚¬", "E")
    text = text.replace("1E", "[E").replace("1€", "[E").replace("1Â£", "[E")
    text = re.sub(r"[\{\(\[]\s*E?([0-9]{4,})[\]\)\}]", r"[E\1]", text)
    text = re.sub(r"\[\s*E?([0-9]{4,})[\]\)\}]", r"[E\1]", text)
    return re.sub(r"\s+", " ", text).strip()


def is_columnar_purchase_item_start(line: str) -> bool:
    return bool(
        re.search(r"(?:^|\s)\d{1,3}\s+\[E?[0-9]{4,}\]", line)
        or re.search(r"\[E?[0-9]{4,}\]", line)
        or re.search(r"(?:^|\s)\d{1,3}\s+\[[0-9]{4,}\]", line)
    )


def clean_columnar_product_description(value: str) -> str:
    text = str(value or "")
    text = re.split(
        r"\b(?:lot|exp\.?\s*date|price|discount|excl\.?\s*vat|incl\.?\s*vat|v\s*code)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.split(r"\b[0-9]{5}[- ][0-9]{2}\b", text, maxsplit=1)[0]
    text = re.split(r"\b\d{2}\s*-\s*\d{4,6}\s*-\s*\d{2}\b", text, maxsplit=1)[0]
    text = re.split(r"\b[0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4}\b", text, maxsplit=1)[0]
    pack_match = re.search(r"^(.+\([^)]+\))", text)
    if pack_match:
        text = pack_match.group(1)
    text = re.sub(r"\b[0-9]{5,}[A-Z0-9-]*\b", " ", text)
    text = re.sub(r"\b[0-9]+[A-Z]\b", " ", text)
    text = re.sub(r"\b[0-9]{2,3}[-.,]\b", " ", text)
    text = re.sub(r"\b[0-9]{1,3}\s+(?=[A-Z][a-z])", " ", text)
    text = re.sub(r"\b(?:AED|DHS|VAT|QTY|PCS|NOS|EA|UNIT|DISCOUNT|PRICE|EXCL|INCL)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+[A-Z]{1,3}[.,]?\s*$", " ", text)
    text = re.sub(r"[^\w\s&+/'().,-]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" :-.,")[:180]


def parse_columnar_purchase_item(
    parts: list[str],
    invoice_no: str,
    date: str,
    supplier: str,
    trn: str,
    pay_term: str = "",
) -> dict[str, Any] | None:
    text = normalize_ocr_item_line(" ".join(parts))
    money = money_values_in_line(text)
    item_match = re.search(r"(?:^|\s)(?:\d{1,3}\s+)?\[?E?([0-9]{4,})\]?\s*(.+)", text)
    if not item_match:
        return None
    sku = "E" + item_match.group(1).zfill(6)
    product = clean_columnar_product_description(item_match.group(2))
    if not product or re.search(r"\b(description|invoice|total|subtotal|vat)\b", product, flags=re.IGNORECASE):
        return None
    if not money:
        return None
    qty = extract_columnar_quantity(text, item_match.end())
    price_before_tax = decimal_value(money[0])
    price_after_discount = decimal_value(money[-4]) if len(money) >= 6 else price_before_tax
    line_total, vat_amount = columnar_line_net_and_vat(money, price_before_tax)
    if line_total <= 0:
        return None
    return normalize_purchase_row({
        "invoice_no": invoice_no or "PDF-INVOICE",
        "date": date,
        "supplier": supplier or "Supplier",
        "supplier_trn": trn,
        "pay_term": pay_term,
        "sku": sku,
        "product": product[:180],
        "quantity": qty,
        "unit": "PCS",
        "unit_cost": price_before_tax,
        "discount": Decimal("0"),
        "unit_cost_before_tax": price_after_discount,
        "vat_amount": vat_amount,
        "line_total": line_total,
        "tax_type": "VAT 5%" if vat_amount else "None",
    })


def extract_columnar_quantity(text: str, item_match_end: int = 0) -> Decimal:
    patterns = (
        r"\b[0-9]{4,5}-[0-9]{2}\s*[=\-]?\s+([0-9]+(?:[.,][0-9]+)?)\s+(?:[A-Z]{0,3})?[0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4}\b",
        r"\b[0-9]{4,5}-[0-9]{2}\s*[=\-]?\s+([0-9]+(?:[.,][0-9]+)?)\s+[A-Z]{1,4}[0-9]{5,8}\b",
        r"\b[A-Z]{2,}[0-9]{2}\s+([0-9]+(?:[.,][0-9]+)?)\s+[A-Z0-9]{5,}\s+[0-9]",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            qty = decimal_value(match.group(1))
            if qty > 0:
                return qty
    before_money = re.split(r"\b[0-9][0-9,]*[.,][0-9]{1,2}\b", text[item_match_end:], maxsplit=1)[0]
    candidates = [
        decimal_value(value)
        for value in re.findall(r"(?<![A-Z0-9])([1-9][0-9]{0,2})(?![A-Z0-9])", before_money)
    ]
    candidates = [value for value in candidates if Decimal("0") < value <= Decimal("999")]
    return candidates[-1] if candidates else Decimal("1")


def columnar_line_net_and_vat(money: list[str], unit_price: Decimal) -> tuple[Decimal, Decimal]:
    values = [decimal_value(value) for value in money]
    if len(values) >= 5:
        return values[-3], values[-2]
    if len(values) == 4:
        line_total = values[-3]
        vat = values[-2]
        gross = values[-1]
        expected_vat = gross - line_total
        if expected_vat > 0 and (vat <= 0 or vat > line_total * Decimal("0.20")):
            vat = expected_vat
        return line_total, vat
    if len(values) == 3:
        vat = values[-2]
        gross = values[-1]
        line_total = gross - vat if gross > vat else values[0]
        return line_total, vat
    if len(values) == 2:
        return values[-1], Decimal("0")
    return unit_price, Decimal("0")


def purchase_item_rows_from_lines(lines: list[str], invoice_no: str, date: str, supplier: str, trn: str, pay_term: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(purchase_columnar_item_rows_from_lines(lines, invoice_no, date, supplier, trn, pay_term))
    if rows:
        return rows
    pending_description = ""
    item_section_started = False
    for line in lines:
        lower = line.lower()
        has_item_header = re.search(r"\b(description|item|product|particulars).{0,40}\b(qty|quantity|rate|price|amount|total)\b", lower)
        if has_item_header:
            item_section_started = True
            if not re.search(r"(?<![A-Z0-9])([0-9][0-9,]*\.[0-9]{2})(?![A-Z0-9])", line):
                continue
            line = re.sub(r"\b(description|item|product|particulars|qty|quantity|rate|price|amount|total|unit)\b", " ", line, flags=re.IGNORECASE)
        if is_document_summary_line(line):
            continue
        money = money_values_in_line(line)
        if not money:
            if item_section_started and looks_like_item_description(line):
                pending_description = line
            continue
        # Prefer qty adjacent to a unit keyword; fall back to first whole integer
        # after the description text (skip leading serial/line numbers at pos 0)
        qty_unit_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:pcs|nos|each|ea|units?|kg|ltr|mtr|box|btl|can|ctn|set)\b", line, flags=re.IGNORECASE)
        description = re.sub(r"\b[0-9][0-9,]*(?:\.[0-9]{1,2})?\b", " ", line)
        description = re.sub(r"\b(?:AED|VAT|TOTAL|SUBTOTAL|TAX|QTY|PCS|NOS)\b", " ", description, flags=re.IGNORECASE)
        description = re.sub(r"\s+", " ", description).strip(" :-")
        if pending_description and (not description or len(description) < 4 or description.replace(".", "").isdigit()):
            description = pending_description
        if not description or re.search(r"\b(total|subtotal|vat|tax|amount due|balance|invoice|date|trn)\b", description, flags=re.IGNORECASE):
            continue
        if qty_unit_match:
            qty = decimal_value(qty_unit_match.group(1)) or Decimal("1")
        else:
            # Skip the first token if it looks like a row serial number (1–3 digits at line start)
            qty_line = re.sub(r"^\s*[0-9]{1,3}\s+", "", line)
            qty_match = re.search(r"(?:^|\s)([1-9][0-9]{0,2})(?:\s|$)", qty_line)
            qty = decimal_value(qty_match.group(1) if qty_match else 1) or Decimal("1")
        # Select line net (excl. VAT): if last 3 money values fit the pattern
        # [net, vat≈net×5%, gross≈net×1.05] then use money[-3] not money[-1]
        money_vals = [decimal_value(m) for m in money]
        if len(money_vals) >= 3:
            net_candidate = money_vals[-3]
            vat_candidate = money_vals[-2]
            gross_candidate = money_vals[-1]
            if (net_candidate > 0 and vat_candidate > 0
                    and abs(vat_candidate - net_candidate * Decimal("0.05")) <= net_candidate * Decimal("0.02") + Decimal("0.10")):
                line_total = net_candidate
            else:
                line_total = money_vals[-1]
        else:
            line_total = money_vals[-1] if money_vals else Decimal("0")
        if line_total <= 0:
            continue
        unit_cost = line_total / qty if qty else line_total
        rows.append(normalize_purchase_row({
            "invoice_no": invoice_no or "PDF-INVOICE",
            "date": date,
            "supplier": supplier or "Supplier",
            "supplier_trn": trn,
            "product": description[:180],
            "quantity": qty,
            "unit": "PCS",
            "unit_cost": unit_cost,
            "line_total": line_total,
            "tax_type": "VAT 5%",
        }))
        pending_description = ""
        if len(rows) >= 200:
            break
    return rows


def money_values_in_line(line: str) -> list[str]:
    line = split_glued_ocr_money_values(str(line or ""))
    values = re.findall(
        r"(?<![A-Z0-9])(?:AED|Dhs\.?|د\.إ|Ø¯\.Ø¥)\s*([0-9][0-9,]*(?:[.,][0-9]{1,2})?)|(?<![A-Z0-9])([0-9][0-9,]*(?:[.,][0-9]{1,2}))(?![A-Z0-9])",
        line,
        flags=re.IGNORECASE,
    )
    money_pattern = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:[.,][0-9]{1,2})?"
    decimal_money_pattern = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)[.,][0-9]{1,2}"
    currency_pattern = r"AED|Dhs\.?|Ø¯\.Ø¥|Ã˜Â¯\.Ã˜Â¥"
    values = re.findall(
        rf"(?<![A-Z0-9])(?:{currency_pattern})\s*({money_pattern})(?![A-Z0-9])|(?<![A-Z0-9])({decimal_money_pattern})(?![A-Z0-9])",
        line,
        flags=re.IGNORECASE,
    )
    cleaned: list[str] = []
    for prefixed, decimal_text in values:
        value = prefixed or decimal_text
        number = decimal_value(value)
        if number > 0:
            cleaned.append(str(number))
    return cleaned


def split_glued_ocr_money_values(line: str) -> str:
    text = str(line or "")
    previous = None
    while previous != text:
        previous = text
        text = re.sub(
            r"([0-9][0-9,]*\.[0-9]{1,2})(?=[0-9]{1,6}[.,][0-9]{1,2}(?![0-9]))",
            r"\1 ",
            text,
        )
    return text


def is_document_summary_line(line: str) -> bool:
    return bool(re.search(r"\b(grand\s+total|invoice\s+total|subtotal|sub\s+total|vat|tax\s+amount|amount\s+due|balance|paid|change|round\s*off)\b", line, flags=re.IGNORECASE))


def looks_like_item_description(line: str) -> bool:
    text = line.strip()
    if len(text) < 3 or len(text) > 180:
        return False
    if re.search(r"\b(invoice|supplier|vendor|seller|trn|date|total|subtotal|vat|tax)\b", text, flags=re.IGNORECASE):
        return False
    return any(ch.isalpha() for ch in text)


def purchase_excel_debug_hint(content: bytes, ext: str) -> str:
    if ext == "pdf":
        return ". Text-based PDFs are supported, including PDFs exported from the Excel template. Scanned PDFs need OCR plus a PDF image renderer on the server."
    if ext in PURCHASE_IMAGE_EXTENSIONS:
        return ". Image extraction needs Tesseract OCR installed on the server."
    try:
        if ext in {"xlsx", "xlsm"}:
            with zipfile.ZipFile(io.BytesIO(content)) as workbook:
                shared_strings = read_xlsx_shared_strings(workbook)
                previews: list[str] = []
                for sheet_name in xlsx_sheet_names(workbook)[:3]:
                    root = ElementTree.fromstring(workbook.read(sheet_name))
                    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                    for row in root.findall(".//x:sheetData/x:row", ns)[:12]:
                        cells = [read_xlsx_cell(cell, shared_strings, ns) for cell in row.findall("x:c", ns)]
                        text = ", ".join(cell for cell in cells if cell.strip())
                        if text:
                            previews.append(text[:180])
                        if len(previews) >= 3:
                            break
                    if previews:
                        break
                return f". First rows detected: {' | '.join(previews)}" if previews else ""
        if ext == "xls":
            text = content.decode("utf-8-sig", errors="ignore")
            first_row = re.search(r"<tr\b[^>]*>(.*?)</tr>", text, flags=re.IGNORECASE | re.DOTALL)
            if first_row:
                cells = [
                    html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell))).strip()
                    for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", first_row.group(1), flags=re.IGNORECASE | re.DOTALL)
                ]
                cells = [cell for cell in cells if cell]
                return f". First row detected: {', '.join(cells[:8])}" if cells else ""
    except Exception:
        return ""
    return ""


PURCHASE_FIELD_ALIASES = {
    "invoice_no": (
        "invoice_no",
        "invoice_number",
        "invoice",
        "bill_no",
        "bill_number",
        "ref",
        "reference",
        "reference_no",
        "purchase_no",
        "purchase_number",
        "voucher_no",
    ),
    "date": ("date", "invoice_date", "bill_date", "purchase_date", "voucher_date", "entry_date"),
    "supplier": ("supplier", "vendor", "vendor_name", "supplier_name", "party", "party_name"),
    "bill_to": ("bill_to", "buyer", "customer", "client", "bill_to_name", "customer_name", "buyer_name"),
    "currency": ("currency", "currency_code", "curr"),
    "address": ("address", "supplier_address", "vendor_address", "billing_address", "bill_to_address"),
    "pay_term": ("pay_term", "payment_term", "payment_terms", "terms", "credit_terms"),
    "supplier_trn": ("supplier_trn", "vendor_trn", "trn", "tax_registration_number", "vat_no", "vat_number"),
    "category": ("category", "item_category", "product_category", "group", "item_group", "class", "brand"),
    "sku": ("sku", "item_code", "product_code", "code", "barcode", "stock_code", "item_no", "product_no"),
    "product": (
        "product",
        "item",
        "item_name",
        "product_name",
        "product_description",
        "description",
        "particulars",
        "item_description",
        "description_of_goods",
        "goods_description",
        "goods",
        "material",
        "name",
    ),
    "unit": ("unit", "uom", "unit_of_measure", "measure", "u_m", "units", "unit_name"),
    "quantity": ("quantity", "qty", "qnty", "purchase_qty", "purchase_quantity", "pcs", "nos", "no", "qty_in", "qty_invoiced"),
    "unit_cost": (
        "unit_cost",
        "cost",
        "price",
        "rate",
        "purchase_price",
        "cost_price",
        "unit_price",
        "unit_cost_before_discount",
        "purchase_unit_cost",
        "basic_rate",
        "u_price",
        "u_rate",
        "mrp",
    ),
    "discount": ("discount", "discount_amount", "disc", "disc_amount"),
    "discount_type": ("discount_type", "purchase_discount_type"),
    "discount_value": ("discount_value", "purchase_discount", "overall_discount", "bill_discount"),
    "profit_margin": ("profit_margin", "margin", "profit_margin_percent", "profit_margin_pct"),
    "selling_price_inc_tax": ("selling_price_inc_tax", "selling_price", "sale_price", "unit_selling_price_inc_tax"),
    "vat_amount": ("vat_amount", "tax_amount", "purchase_tax", "vat", "tax", "gst", "igst", "cgst", "sgst", "vat_5", "vat_5_percent"),
    "tax_type": ("tax_type", "vat_type", "vat_rate", "tax_rate"),
    "shipping_details": ("shipping_details", "shipping_detail", "delivery_details", "transport_details"),
    "shipping": ("shipping", "shipping_charges", "freight", "freight_charges", "delivery_charges"),
    "paid": ("paid", "paid_amount", "amount_paid", "payment_amount"),
    "paid_on": ("paid_on", "payment_date", "paid_date"),
    "payment_method": ("payment_method", "pay_method", "mode_of_payment"),
    "payment_account": ("payment_account", "paid_from", "bank_account"),
    "payment_note": ("payment_note", "payment_notes", "payment_reference"),
    "notes": ("notes", "remarks", "narration", "comments"),
    "line_total": (
        "line_total",
        "amount",
        "total",
        "net_amount",
        "taxable_amount",
        "taxable_value",
        "gross_amount",
        "gross_value",
        "net_value",
        "value",
    ),
}


def normalize_purchase_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {normalize_header(key): value for key, value in row.items()}
    mapped = {field: first_present(normalized, names) for field, names in PURCHASE_FIELD_ALIASES.items()}
    if mapped.get("date"):
        mapped["date"] = normalize_document_date(mapped["date"])
    mapped["raw"] = {normalize_header(key): value for key, value in row.items() if str(value or "").strip()}
    return mapped


def normalize_header(value: Any) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "").strip()).strip("_")


def first_present(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    for key, value in row.items():
        if value in (None, ""):
            continue
        if any(name in key for name in names if len(name) >= 4):
            return value
    return ""


def build_purchase_invoices_from_rows(
    db: Session,
    principal: Principal,
    rows: list[dict[str, Any]],
    filename: str,
) -> list[dict[str, Any]]:
    # Resolved once, not per-row/per-call, to avoid an N+1 query on large
    # bulk-purchase-upload files (this function can process hundreds of rows).
    company = resolve_principal_company(principal, db)
    grouped: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, start=1):
        product = str(row.get("product") or row.get("sku") or row.get("category") or "").strip()
        if not product:
            continue
        invoice_no = str(row.get("invoice_no") or clean_base(filename)).strip()
        category = str(row.get("category") or "Uncategorized").strip()
        unit = str(row.get("unit") or "PCS").strip().upper()
        sku = str(row.get("sku") or product_code(product, index)).strip().upper()
        supplier = str(row.get("supplier") or "Supplier").strip()
        quantity = decimal_value(row.get("quantity") or 1)
        unit_cost = decimal_value(row.get("unit_cost"))
        discount_percent = decimal_value(row.get("discount"))
        unit_cost_before_tax = unit_cost * (Decimal("1") - (discount_percent / Decimal("100")))
        line_total = decimal_value(row.get("line_total")) or (quantity * unit_cost_before_tax)
        vat = decimal_value(row.get("vat_amount"))
        upsert_purchase_master_data(db, principal, category, unit, sku, product, supplier, unit_cost)

        invoice = grouped.setdefault(
            invoice_no,
            {
                "invoice_no": invoice_no,
                "date": excel_date_value(row.get("date")),
                "supplier": supplier,
                "bill_to": str(row.get("bill_to") or ""),
                "currency": str(row.get("currency") or (company.currency if company else None) or "AED"),
                "address": str(row.get("address") or ""),
                "pay_term": str(row.get("pay_term") or ""),
                "supplier_trn": str(row.get("supplier_trn") or ""),
                "subtotal": Decimal("0"),
                "vat_amount": Decimal("0"),
                "total": Decimal("0"),
                "discount_type": str(row.get("discount_type") or "None"),
                "discount_value": decimal_value(row.get("discount_value")),
                "tax_type": str(row.get("tax_type") or ("VAT 5%" if vat else "None")),
                "shipping_details": str(row.get("shipping_details") or ""),
                "shipping": decimal_value(row.get("shipping")),
                "paid": decimal_value(row.get("paid")),
                "paid_on": excel_date_value(row.get("paid_on")),
                "payment_method": str(row.get("payment_method") or "Cash"),
                "payment_account": str(row.get("payment_account") or "None"),
                "payment_note": str(row.get("payment_note") or ""),
                "notes": str(row.get("notes") or ""),
                "confidence": 0,  # scored below once the invoice is complete
                "status": "Valid",
                "issues": "",
                "lines": [],
            },
        )
        invoice["subtotal"] += line_total
        invoice["vat_amount"] += vat
        invoice["lines"].append(
            {
                "sku": sku,
                "category": category,
                "product": product,
                "unit": unit,
                "quantity": float(quantity),
                "unit_cost": float(unit_cost),
                "unit_price": float(unit_cost),
                "discount_percent": float(discount_percent),
                "discount_amount": float(unit_cost * quantity * (discount_percent / Decimal("100"))) if discount_percent else float(decimal_value(row.get("discount_amount"))),
                "unit_cost_before_tax": float(unit_cost_before_tax),
                "line_total": float(line_total),
                "profit_margin": float(decimal_value(row.get("profit_margin"))),
                "selling_price_inc_tax": float(decimal_value(row.get("selling_price_inc_tax"))),
                "raw": row.get("raw") or {},
            }
        )
    company_vat_rate = get_company_vat_rate(company)
    invoices = []
    for invoice in grouped.values():
        discount_type = str(invoice.get("discount_type") or "None")
        discount_value = decimal_value(invoice.get("discount_value"))
        discount = invoice["subtotal"] * (discount_value / Decimal("100")) if discount_type == "Percentage" else discount_value if discount_type == "Fixed" else Decimal("0")
        taxable = max(Decimal("0"), invoice["subtotal"] - discount)
        if not invoice["vat_amount"] and "5%" in str(invoice.get("tax_type") or "") and "exempt" not in str(invoice.get("tax_type") or "").lower():
            invoice["vat_amount"] = taxable * (company_vat_rate / Decimal("100"))
        invoice["total"] = taxable + invoice["vat_amount"] + decimal_value(invoice.get("shipping"))
        invoice["due"] = max(Decimal("0"), invoice["total"] - decimal_value(invoice.get("paid")))
        invoice["confidence"] = extraction_confidence(
            invoice_no_found=invoice["invoice_no"] != clean_base(filename), party=invoice["supplier"],
            date=invoice["date"], subtotal=invoice["subtotal"], vat=invoice["vat_amount"], total=invoice["total"],
            line_count=len(invoice["lines"]), check_totals=False,  # total is computed from the lines here
        )
        invoices.append(json.loads(json.dumps(invoice, default=float)))
    return invoices


def merge_purchase_invoices(invoices: list[dict[str, Any]], filename: str = "") -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for index, invoice in enumerate(invoices or [], start=1):
        invoice_no = str(invoice.get("invoice_no") or invoice.get("ref") or f"{clean_base(filename)}-{index}").strip()
        key = invoice_no.lower()
        target = grouped.setdefault(
            key,
            {
                **invoice,
                "invoice_no": invoice_no,
                "subtotal": Decimal("0"),
                "vat_amount": Decimal("0"),
                "total": Decimal("0"),
                "paid": Decimal("0"),
                "shipping": Decimal("0"),
                "confidence": 0,
                "lines": [],
            },
        )
        for field in (
            "date",
            "supplier",
            "supplier_trn",
            "address",
            "pay_term",
            "discount_type",
            "tax_type",
            "payment_method",
            "payment_account",
            "payment_note",
            "paid_on",
            "shipping_details",
            "notes",
            "status",
            "issues",
        ):
            if not target.get(field) and invoice.get(field):
                target[field] = invoice.get(field)
        target["subtotal"] = max(decimal_value(target.get("subtotal")), decimal_value(invoice.get("subtotal") or invoice.get("net_amount")))
        target["vat_amount"] = max(decimal_value(target.get("vat_amount")), decimal_value(invoice.get("vat_amount") or invoice.get("tax_amount")))
        target["total"] = max(decimal_value(target.get("total")), decimal_value(invoice.get("total")))
        target["paid"] = max(decimal_value(target.get("paid")), decimal_value(invoice.get("paid")))
        target["shipping"] = max(decimal_value(target.get("shipping")), decimal_value(invoice.get("shipping")))
        target["confidence"] = max(int(decimal_value(target.get("confidence"))), int(decimal_value(invoice.get("confidence"))))
        for line in invoice.get("lines") or []:
            target["lines"].append(line)
    merged = []
    for invoice in grouped.values():
        line_total = sum((decimal_value(line.get("line_total") or line.get("amount")) for line in invoice.get("lines") or []), Decimal("0"))
        if line_total and (not decimal_value(invoice.get("subtotal")) or len(invoice.get("lines") or []) > 1):
            invoice["subtotal"] = max(decimal_value(invoice.get("subtotal")), line_total)
        if not decimal_value(invoice.get("total")):
            invoice["total"] = decimal_value(invoice.get("subtotal")) + decimal_value(invoice.get("vat_amount")) + decimal_value(invoice.get("shipping"))
        invoice["due"] = max(Decimal("0"), decimal_value(invoice.get("total")) - decimal_value(invoice.get("paid")))
        invoice["items"] = len(invoice.get("lines") or [])
        merged.append(json.loads(json.dumps(invoice, default=float)))
    return merged


def excel_date_value(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        serial = int(float(raw))
        if 20000 <= serial <= 80000:
            from datetime import date, timedelta

            return (date(1899, 12, 30) + timedelta(days=serial)).isoformat()
    except (TypeError, ValueError):
        pass
    return raw


def upsert_purchase_master_data(
    db: Session,
    principal: Principal,
    category: str,
    unit: str,
    sku: str,
    product: str,
    supplier: str,
    cost: Decimal,
) -> None:
    save_app_record(db, principal, "salesCategories", {"name": category, "type": "Purchase", "status": "Active"})
    product_record = {
        "code": sku,
        "name": product,
        "category": category,
        "unit": unit,
        "cost": float(cost),
        "vat": "Standard 5%",
        "supplier_name": supplier,
        "status": "Active",
    }
    saved = save_app_record(db, principal, "products", product_record)
    sync_domain_model(db, principal, "products", serialize(saved))


def product_code(product: str, index: int) -> str:
    base = "".join(ch for ch in product.upper() if ch.isalnum())[:12] or "ITEM"
    return f"{base}-{index:03d}"


def purchase_extraction_error(filename: str, issue: str) -> dict[str, Any]:
    ref = f"{clean_base(filename)}-REVIEW"
    return {
        "invoice_no": ref,
        "date": "",
        "supplier": "",
        "supplier_trn": "",
        "subtotal": 0,
        "vat_amount": 0,
        "total": 0,
        "confidence": 0,
        "status": "Error",
        "extraction_error": True,
        "issues": issue,
        "lines": [],
    }


def clean_base(name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in stem).strip("-").upper()
    return (cleaned or "TAXFLOW")[:18]


# Collections that hold user config/templates — preserved on wipe
_WIPE_KEEP_COLLECTIONS = frozenset({
    "invoiceLayout",
    "invoice-layouts-pack",
    "salesCategories",
    "salesUnits",
})


class WipeCompanyDataIn(BaseModel):
    confirm: str = ""


@router.post("/wipe", dependencies=[Depends(require_company_admin)])
@limiter.limit("5/minute")
def wipe_company_data(
    request: Request,
    payload: WipeCompanyDataIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete all transactional data for the company. Keeps invoice layouts,
    sales categories and sales units. Irreversible — requires explicit call."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only an admin can wipe company data")
    if payload.confirm.strip().upper() != "DELETE ALL":
        raise HTTPException(status_code=422, detail='Confirmation phrase "DELETE ALL" is required')
    cid = current_user.company_id

    # Domain tables (order matters for FK constraints)
    db.query(AuditLogDetail).filter(
        AuditLogDetail.audit_log_id.in_(
            db.query(AuditLog.id).filter(AuditLog.company_id == cid)
        )
    ).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.company_id == cid).delete(synchronize_session=False)
    db.query(InventoryValuationLayer).filter(InventoryValuationLayer.company_id == cid).delete(synchronize_session=False)
    db.query(StockMovement).filter(StockMovement.company_id == cid).delete(synchronize_session=False)
    db.query(TaxLine).filter(TaxLine.company_id == cid).delete(synchronize_session=False)
    db.query(JournalLine).filter(
        JournalLine.journal_id.in_(
            db.query(JournalEntry.id).filter(JournalEntry.company_id == cid)
        )
    ).delete(synchronize_session=False)
    db.query(JournalEntry).filter(JournalEntry.company_id == cid).delete(synchronize_session=False)
    db.query(InvoiceLine).filter(
        InvoiceLine.invoice_id.in_(
            db.query(Invoice.id).filter(Invoice.company_id == cid)
        )
    ).delete(synchronize_session=False)
    db.query(Invoice).filter(Invoice.company_id == cid).delete(synchronize_session=False)
    db.query(Payment).filter(Payment.company_id == cid).delete(synchronize_session=False)
    db.query(Receipt).filter(Receipt.company_id == cid).delete(synchronize_session=False)
    db.query(SourceTransactionLine).filter(
        SourceTransactionLine.source_id.in_(
            db.query(SourceTransaction.id).filter(SourceTransaction.company_id == cid)
        )
    ).delete(synchronize_session=False)
    db.query(SourceTransaction).filter(SourceTransaction.company_id == cid).delete(synchronize_session=False)
    db.query(PayrollRun).filter(PayrollRun.company_id == cid).delete(synchronize_session=False)
    db.query(Employee).filter(Employee.company_id == cid).delete(synchronize_session=False)
    db.query(StockProductMapping).filter(StockProductMapping.company_id == cid).delete(synchronize_session=False)

    # App data records (all transactional collections)
    app_deleted = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == cid,
            AppDataRecord.collection.notin_(_WIPE_KEEP_COLLECTIONS),
        )
        .delete(synchronize_session=False)
    )

    db.commit()

    # Logged after the wipe (not before) so this record survives the
    # AuditLog deletion above instead of being wiped along with everything else.
    # log_action() takes a Principal now (Branch Management Phase 4) — this
    # endpoint stays strictly admin-only (see the role check above), so
    # wrap current_user in an admin-shaped Principal just for this one call
    # rather than widening this destructive endpoint's own auth.
    log_action(
        db,
        Principal(kind="user", company_id=current_user.company_id, display_name=current_user.full_name, is_admin=True, user=current_user),
        "settings", "company_data_wiped", {"app_records_deleted": app_deleted},
    )
    db.commit()

    import app.cache as cache
    cache.delete(f"summary:{cid}")
    cache.delete(f"dashboard:{cid}")

    return {"ok": True, "app_records_deleted": app_deleted}
