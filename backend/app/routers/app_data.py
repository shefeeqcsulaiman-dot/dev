import base64
import csv
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
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.module_integration import sync_purchase_accounting, sync_sales_invoice_accounting
from app.models import (
    Account,
    AppDataRecord,
    AuditLog,
    Employee,
    InventoryValuationLayer,
    Invoice,
    InvoiceLine,
    SourceTransaction,
    SourceTransactionLine,
    StockMovement,
    StockProductMapping,
    User,
)


router = APIRouter(prefix="/app-data", tags=["app data"])


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
        "salesCategories": "name",
        "salesUnits": "code",
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
    }
    field = keys.get(collection)
    if field and record.get(field):
        return str(record[field])
    for field in ("id", "reference", "name", "code", "ref"):
        if record.get(field):
            return str(record[field])
    return None


def serialize(record: AppDataRecord) -> dict[str, Any]:
    try:
        return json.loads(record.payload)
    except json.JSONDecodeError:
        return {}


@router.get("/records/{collection}")
def list_collection_records(
    collection: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    total = (
        db.query(func.count(AppDataRecord.id))
        .filter(
            AppDataRecord.company_id == current_user.company_id,
            AppDataRecord.collection == collection,
        )
        .scalar()
        or 0
    )
    rows = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == current_user.company_id,
            AppDataRecord.collection == collection,
        )
        .order_by(AppDataRecord.created_at.desc(), AppDataRecord.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "ok": True,
        "collection": collection,
        "records": [serialize(row) for row in rows],
        "limit": limit,
        "offset": offset,
        "total": total,
        "has_more": offset + len(rows) < total,
    }


@router.get("")
def bootstrap(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    records = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == current_user.company_id)
        .order_by(AppDataRecord.created_at.asc())
        .all()
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        if item.collection == "purchaseRecords":
            continue
        grouped.setdefault(item.collection, []).append(serialize(item))

    audit_rows = (
        db.query(AuditLog)
        .filter(AuditLog.company_id == current_user.company_id)
        .order_by(AuditLog.created_at.desc())
        .limit(50)
        .all()
    )
    audit = [
        {
            "time": row.created_at.strftime("%d/%m/%Y, %H:%M") if row.created_at else "",
            "user": current_user.full_name,
            "action": row.action.replace("_", " ").title(),
            "record": row.module,
            "result": "Logged",
        }
        for row in audit_rows
    ]

    invoice_layout = grouped.get("invoiceLayout", [{}])[-1] if grouped.get("invoiceLayout") else None
    data: dict[str, object] = {
        **grouped,
        "audit": audit,
        "invoiceLayout": invoice_layout,
        "user": {"name": current_user.full_name, "role": current_user.role},
    }
    return {"ok": True, "data": data}


@router.post("")
async def app_data_action(
    request: Request,
    action: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    payload = await request.json()
    if action == "save":
        collection = str(payload.get("collection", "app_actions"))
        record = payload.get("record", {})
        if not isinstance(record, dict):
            record = {"value": record}
        saved = save_app_record(db, current_user, collection, record)
        sync_domain_model(db, current_user, collection, serialize(saved))
        db.commit()
        return {"ok": True, "saved": True, "id": saved.id}

    if action == "bulk-save":
        collection = str(payload.get("collection", "app_actions"))
        records = payload.get("records", [])
        if not isinstance(records, list):
            records = []
        normalized_records = [record if isinstance(record, dict) else {"value": record} for record in records]
        keys = [key for key in (record_key(collection, record) for record in normalized_records) if key]
        existing_by_key = {
            item.record_key: item
            for item in db.query(AppDataRecord)
            .filter(
                AppDataRecord.company_id == current_user.company_id,
                AppDataRecord.collection == collection,
                AppDataRecord.record_key.in_(keys),
            )
            .all()
        } if keys else {}
        saved_count = 0
        updated_count = 0
        created_count = 0
        for record in normalized_records:
            key = record_key(collection, record)
            payload_json = json.dumps(record, ensure_ascii=False, default=str)
            existing = existing_by_key.get(key) if key else None
            if existing:
                existing.payload = payload_json
                saved = existing
                updated_count += 1
            else:
                saved = AppDataRecord(
                    company_id=current_user.company_id,
                    collection=collection,
                    record_key=key,
                    payload=payload_json,
                )
                db.add(saved)
                if key:
                    existing_by_key[key] = saved
                created_count += 1
            sync_domain_model(db, current_user, collection, record)
            saved_count += 1
        log_action(
            db,
            current_user,
            collection,
            "records_bulk_saved",
            {"count": saved_count, "created": created_count, "updated": updated_count},
        )
        db.commit()
        return {"ok": True, "saved": saved_count, "created": created_count, "updated": updated_count}

    if action == "delete":
        collection = str(payload.get("collection", "app_actions"))
        record = payload.get("record", {})
        if not isinstance(record, dict):
            record = {"value": record}
        key = record_key(collection, record)
        deleted = False
        if key:
            existing = (
                db.query(AppDataRecord)
                .filter(
                    AppDataRecord.company_id == current_user.company_id,
                    AppDataRecord.collection == collection,
                    AppDataRecord.record_key == key,
                )
                .first()
            )
            if existing:
                db.delete(existing)
                deleted = True
            sync_domain_delete(db, current_user, collection, record)
        log_action(db, current_user, collection, "record_deleted" if deleted else "delete_not_found", record)
        db.commit()
        return {"ok": True, "deleted": deleted, "key": key}

    if action == "invoice-layout":
        record = dict(payload)
        saved = save_app_record(db, current_user, "invoiceLayout", record)
        log_action(db, current_user, "settings", "invoice_layout_saved", record)
        db.commit()
        return {"ok": True, "layout": record, "id": saved.id}

    if action == "documents.extract":
        file = payload.get("file", {})
        invoices = ingest_purchase_document(db, current_user, file)
        log_action(
            db,
            current_user,
            "documents",
            "document_extraction_requested",
            {"file": file.get("name"), "invoices": len(invoices)},
        )
        db.commit()
        return {"ok": True, "invoices": invoices}

    if action == "invoices.import":
        file = payload.get("file", {})
        invoices: list[dict[str, Any]] = []
        log_action(db, current_user, "salesInvoices", "invoice_import_requested", {"file": file.get("name"), "result": "no_demo_data"})
        db.commit()
        return {"ok": True, "invoices": invoices}

    return {"ok": True, "action": action}


def save_app_record(db: Session, current_user: User, collection: str, record: dict[str, Any]) -> AppDataRecord:
    key = record_key(collection, record)
    existing = None
    if key:
        existing = (
            db.query(AppDataRecord)
            .filter(
                AppDataRecord.company_id == current_user.company_id,
                AppDataRecord.collection == collection,
                AppDataRecord.record_key == key,
            )
            .first()
        )
    payload = json.dumps(record, ensure_ascii=False, default=str)
    if existing:
        existing.payload = payload
        saved = existing
    else:
        saved = AppDataRecord(
            company_id=current_user.company_id,
            collection=collection,
            record_key=key,
            payload=payload,
        )
        db.add(saved)
    log_action(db, current_user, collection, "record_saved", record)
    return saved


def sync_domain_model(db: Session, current_user: User, collection: str, record: dict[str, Any]) -> None:
    if collection == "products":
        code = str(record.get("code") or record.get("sku") or "").strip()
        name = str(record.get("name") or "").strip()
        if code and name:
            mapping = (
                db.query(StockProductMapping)
                .filter(StockProductMapping.company_id == current_user.company_id, StockProductMapping.sku == code)
                .first()
            )
            if not mapping:
                mapping = StockProductMapping(company_id=current_user.company_id, sku=code, name=name)
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

    elif collection == "accounts":
        code = str(record.get("code") or "").strip()
        name = str(record.get("name") or "").strip()
        if code and name:
            account = (
                db.query(Account)
                .filter(Account.company_id == current_user.company_id, Account.code == code)
                .first()
            )
            if not account:
                account = Account(company_id=current_user.company_id, code=code, name=name, type=str(record.get("type") or "asset").lower())
                db.add(account)
            account.name = name
            account.type = str(record.get("type") or account.type).lower()

    elif collection == "salesInvoices":
        sync_sales_invoice(db, current_user, record)

    elif collection == "bills":
        sync_source_transaction(
            db,
            current_user,
            module="purchase_bill",
            reference=str(record.get("bill_no") or "BILL"),
            party_name=str(record.get("vendor") or ""),
            subtotal=decimal_value(record.get("subtotal")),
            vat=decimal_value(record.get("vat")),
            total=decimal_value(record.get("total")),
            status=str(record.get("status") or "draft"),
        )

    elif collection == "purchaseRecords":
        reference = str(record.get("ref") or record.get("invoice_no") or "PURCHASE")
        sync_purchase_accounting(
            db,
            company_id=current_user.company_id,
            reference=reference,
            party_name=str(record.get("supplier") or ""),
            subtotal=decimal_value(record.get("net_amount") or record.get("subtotal")),
            vat=decimal_value(record.get("tax_amount") or record.get("vat_amount")),
            total=decimal_value(record.get("total")),
            lines=record.get("lines") if isinstance(record.get("lines"), list) else None,
            user_id=current_user.id,
        )
        sync_purchase_stock(db, current_user, record, reference)

    elif collection == "payments":
        amount = decimal_value(record.get("amount"))
        sync_source_transaction(
            db,
            current_user,
            module="payment",
            reference=str(record.get("ref") or "PAYMENT"),
            party_name=str(record.get("contact") or ""),
            subtotal=amount,
            vat=Decimal("0"),
            total=amount,
            status="posted",
        )

    elif collection in ("employees", "staff"):
        emp_no = str(record.get("id") or record.get("employee_no") or record.get("emp_no") or "").strip()
        full_name = str(record.get("name") or record.get("full_name") or "").strip()
        if emp_no and full_name:
            emp = (
                db.query(Employee)
                .filter(Employee.company_id == current_user.company_id, Employee.employee_no == emp_no)
                .first()
            )
            if not emp:
                emp = Employee(company_id=current_user.company_id, employee_no=emp_no, full_name=full_name)
                db.add(emp)
            emp.full_name = full_name
            emp.department = str(record.get("department") or emp.department or "Operations").strip()
            emp.designation = str(record.get("designation") or record.get("position") or emp.designation or "Staff").strip()
            salary = decimal_value(record.get("salary") or record.get("basic_salary") or 0)
            if salary > 0:
                emp.basic_salary = salary
            iban = str(record.get("iban") or "").strip()
            if iban:
                emp.iban = iban
            status_raw = str(record.get("status") or "Active").lower()
            emp.status = "active" if status_raw in ("active", "1", "true") else "inactive"

    elif collection == "audit":
        log_action(db, current_user, str(record.get("record") or "audit"), str(record.get("action") or "ui_action"), record)


def sync_purchase_stock(db: Session, current_user: User, record: dict[str, Any], reference: str) -> None:
    lines = record.get("lines")
    if not isinstance(lines, list):
        lines = []
    db.query(StockMovement).filter(
        StockMovement.company_id == current_user.company_id,
        StockMovement.movement_type == "purchase",
        StockMovement.reference == reference,
    ).delete(synchronize_session=False)
    db.query(InventoryValuationLayer).filter(
        InventoryValuationLayer.company_id == current_user.company_id,
        InventoryValuationLayer.source_module == "purchase",
        InventoryValuationLayer.source_id == reference,
    ).delete(synchronize_session=False)
    db.flush()

    for line in lines:
        if not isinstance(line, dict):
            continue
        quantity = decimal_value(line.get("quantity") or line.get("qty") or line.get("purchase_qty") or line.get("qty_invoiced"))
        if quantity <= 0:
            continue
        mapping = purchase_line_stock_mapping(db, current_user, line, record)
        if not mapping:
            continue
        unit_cost = decimal_value(
            line.get("unit_cost_before_tax")
            or line.get("unit_cost")
            or line.get("purchase_unit_cost")
            or line.get("cost")
        )
        db.add(
            StockMovement(
                company_id=current_user.company_id,
                mapping_id=mapping.id,
                movement_type="purchase",
                quantity=quantity,
                unit_cost=unit_cost,
                reference=reference,
            )
        )
        db.add(
            InventoryValuationLayer(
                company_id=current_user.company_id,
                item_code=mapping.sku,
                source_module="purchase",
                source_id=reference,
                quantity_in=quantity,
                quantity_remaining=quantity,
                unit_cost=unit_cost,
            )
        )


def purchase_line_stock_mapping(
    db: Session,
    current_user: User,
    line: dict[str, Any],
    record: dict[str, Any],
) -> StockProductMapping | None:
    sku = str(line.get("sku") or line.get("code") or "").strip()
    product = str(line.get("product") or line.get("name") or line.get("description") or "").strip()
    if not sku and not product:
        return None
    mapping = None
    if sku:
        mapping = (
            db.query(StockProductMapping)
            .filter(StockProductMapping.company_id == current_user.company_id, StockProductMapping.sku == sku)
            .first()
        )
    if not mapping and product:
        mapping = (
            db.query(StockProductMapping)
            .filter(StockProductMapping.company_id == current_user.company_id, StockProductMapping.name == product)
            .first()
        )
    if not mapping:
        mapping = StockProductMapping(
            company_id=current_user.company_id,
            sku=sku or product[:60],
            name=product or sku,
            supplier_name=str(record.get("supplier") or "").strip() or None,
        )
        db.add(mapping)
        db.flush()
    if product:
        mapping.name = product
    supplier = str(record.get("supplier") or "").strip()
    if supplier:
        mapping.supplier_name = supplier
    unit_cost = decimal_value(line.get("unit_cost_before_tax") or line.get("unit_cost") or line.get("cost"))
    if unit_cost > 0:
        mapping.cost = unit_cost
    return mapping


def sync_domain_delete(db: Session, current_user: User, collection: str, record: dict[str, Any]) -> None:
    if collection == "products":
        code = str(record.get("code") or record.get("sku") or record.get("id") or "").strip()
        if code:
            mapping = (
                db.query(StockProductMapping)
                .filter(StockProductMapping.company_id == current_user.company_id, StockProductMapping.sku == code)
                .first()
            )
            if mapping:
                db.delete(mapping)

    elif collection == "salesInvoices":
        number = str(record.get("invoice_no") or record.get("invoice_number") or record.get("id") or "").strip()
        if number:
            invoice = (
                db.query(Invoice)
                .filter(Invoice.company_id == current_user.company_id, Invoice.invoice_number == number)
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
                    SourceTransaction.company_id == current_user.company_id,
                    SourceTransaction.module == module,
                    SourceTransaction.reference == reference,
                )
                .first()
            )
            if tx:
                db.query(SourceTransactionLine).filter(SourceTransactionLine.source_id == tx.id).delete(synchronize_session=False)
                db.delete(tx)
            db.query(StockMovement).filter(
                StockMovement.company_id == current_user.company_id,
                StockMovement.movement_type == module,
                StockMovement.reference == reference,
            ).delete(synchronize_session=False)
            db.query(InventoryValuationLayer).filter(
                InventoryValuationLayer.company_id == current_user.company_id,
                InventoryValuationLayer.source_module == module,
                InventoryValuationLayer.source_id == reference,
            ).delete(synchronize_session=False)


def sync_sales_invoice(db: Session, current_user: User, record: dict[str, Any]) -> None:
    number = str(record.get("invoice_no") or record.get("invoice_number") or "").strip()
    customer = str(record.get("customer") or record.get("customer_name") or "Customer").strip()
    if not number:
        return
    invoice = (
        db.query(Invoice)
        .filter(Invoice.company_id == current_user.company_id, Invoice.invoice_number == number)
        .first()
    )
    if not invoice:
        invoice = Invoice(company_id=current_user.company_id, invoice_number=number, customer_name=customer)
        db.add(invoice)
    invoice.customer_name = customer
    invoice.subtotal = decimal_value(record.get("subtotal"))
    invoice.vat = decimal_value(record.get("vat_amount") or record.get("vat"))
    invoice.total = decimal_value(record.get("total"))
    invoice.status = "issued" if str(record.get("status", "")).lower() in {"ready", "pending"} else str(record.get("status") or "draft").lower()
    lines = record.get("lines") if isinstance(record.get("lines"), list) else []
    if lines:
        invoice.lines = []
        for line in lines:
            quantity = decimal_value(line.get("qty") or line.get("quantity") or 1)
            unit_price = decimal_value(line.get("unit_price") or line.get("price_snapshot") or line.get("price"))
            invoice.lines.append(
                InvoiceLine(
                    description=str(line.get("description") or line.get("product_name") or "Invoice item"),
                    quantity=quantity if quantity > 0 else Decimal("1"),
                    unit_price=unit_price,
                    vat_rate=decimal_value(line.get("tax_rate") or line.get("vat_rate") or 5),
                )
            )
    elif not invoice.lines:
        invoice.lines = [
            InvoiceLine(
                description=f"Imported invoice {number}",
                quantity=Decimal("1"),
                unit_price=invoice.subtotal,
                vat_rate=Decimal("5"),
            )
        ]
    db.flush()
    sync_sales_invoice_accounting(db, invoice, current_user.id)


def sync_source_transaction(
    db: Session,
    current_user: User,
    module: str,
    reference: str,
    party_name: str,
    subtotal: Decimal,
    vat: Decimal,
    total: Decimal,
    status: str,
    lines: list[dict[str, Any]] | None = None,
    tax_type: str = "",
) -> SourceTransaction:
    existing = (
        db.query(SourceTransaction)
        .filter(
            SourceTransaction.company_id == current_user.company_id,
            SourceTransaction.module == module,
            SourceTransaction.reference == reference,
        )
        .first()
    )
    tx = existing or SourceTransaction(company_id=current_user.company_id, module=module, reference=reference)
    if not existing:
        db.add(tx)
        db.flush()
    tx.party_name = party_name
    tx.subtotal = subtotal
    tx.vat = vat
    tx.total = total
    tx.status = status.lower().replace(" ", "_")
    lines = lines if isinstance(lines, list) else None
    if lines is not None:
        vat_rate = Decimal("5") if "5%" in tax_type and "exempt" not in tax_type.lower() else Decimal("0")
        db.query(SourceTransactionLine).filter(SourceTransactionLine.source_id == tx.id).delete(synchronize_session=False)
        db.flush()
        for line in lines:
            description = str(line.get("product") or line.get("description") or "").strip()
            if not description:
                continue
            amount = decimal_value(line.get("line_total"))
            db.add(SourceTransactionLine(
                source_id=tx.id,
                description=str(line.get("product") or line.get("description") or "Purchase item")[:255],
                account_code=str(line.get("account_code") or "4000"),
                quantity=decimal_value(line.get("quantity") or line.get("qty") or 1),
                unit_price=decimal_value(line.get("unit_cost_before_tax") or line.get("unit_cost") or line.get("cost")),
                vat_rate=vat_rate,
                amount=amount,
                vat_amount=amount * (vat_rate / Decimal("100")),
            ))
    return tx


def log_action(db: Session, current_user: User, module: str, action: str, detail: Any) -> None:
    db.add(
        AuditLog(
            company_id=current_user.company_id,
            user_id=current_user.id,
            module=str(module)[:60],
            action=str(action)[:80],
            detail=json.dumps(detail, ensure_ascii=False, default=str)[:1000],
        )
    )


def ingest_purchase_document(db: Session, current_user: User, file: dict[str, Any]) -> list[dict[str, Any]]:
    name = str(file.get("name") or "purchase-upload").strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    content = decode_uploaded_file(file)
    if not content:
        return [purchase_extraction_error(name, "Uploaded file content was empty")]

    # For image uploads, check API key early so user gets a clear message
    if ext in PURCHASE_IMAGE_EXTENSIONS:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return [purchase_extraction_error(
                name,
                "AI extraction requires OPENAI_API_KEY. "
                "Go to your Render dashboard → etaxflow → Environment and add OPENAI_API_KEY with your OpenAI key, then redeploy."
            )]

    try:
        if ext == "csv":
            rows = parse_csv_rows(content)
        elif ext in {"xlsx", "xlsm"}:
            rows = parse_xlsx_rows(content)
        elif ext == "xls":
            rows = parse_excel_html_rows(content)
        elif ext == "pdf":
            rows = parse_pdf_purchase_rows(content)
        elif ext in PURCHASE_IMAGE_EXTENSIONS:
            rows = parse_image_purchase_rows(content, ext)
        else:
            return [purchase_extraction_error(name, f"Unsupported purchase upload format: .{ext or 'unknown'}")]
    except Exception as exc:
        return [purchase_extraction_error(name, f"Could not parse file: {exc}")]

    invoices = merge_purchase_invoices(build_purchase_invoices_from_rows(db, current_user, rows, name), name)
    if not invoices:
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


def parse_xlsx_rows(content: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(content)) as workbook:
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
    # Raises RuntimeError if OpenAI key is set but call fails
    ai_rows = extract_purchase_rows_with_openai(content, ext)
    if ai_rows:
        return ai_rows
    text = extract_image_text_with_tesseract(content, ext)
    if not text:
        raise RuntimeError(
            "Could not extract text from image. "
            "Ensure OPENAI_API_KEY is set in Render for AI-based extraction, "
            "or upload a PDF/CSV/Excel file instead."
        )
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
      "sku": "product/vendor code from 'V Code', 'Item Code', 'SKU', 'Product Code', or 'Code' column — empty string if not found",
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
- CRITICAL — every row in the invoice table is a separate line item. Do NOT merge, deduplicate, or skip any row. If two rows have the same quantity, same price, or same amount they are still two separate products — output both.
- CRITICAL — qty: Use ONLY the 'Qty' or 'Quantity' column. NEVER use 'Batch Qty', 'Pack Qty', 'Batch Size', 'Order Qty'. Lot/Batch numbers are NOT quantities.
- CRITICAL — unit_price: Use 'Price Before Discount', 'Rate', 'Unit Price', or 'Price'. Do NOT use 'Price After Discount', 'Lot No', 'Batch No', or 'Expiry Date' as price.
- CRITICAL — line_total_excl_vat: Use 'Excl.Vat', 'Excl. VAT', or 'Amount' column (before VAT). Do NOT use the 'Incl.Vat' or VAT-inclusive total for this field.
- CRITICAL — if invoice shows 'Price Before Discount' AND 'Price After Discount', use 'Price Before Discount' as unit_price and set discount_pct from the Discount % column.
- trn_vat is the SUPPLIER's TRN only — digits only, no spaces or hyphens.
- 'Business Partner TRN', 'Customer TRN', or 'Buyer TRN' on the invoice is the buyer's TRN — do NOT put it in trn_vat.
- total_discount is the invoice-level discount total, not per-line discount.
- Numbers must be plain digits with decimal point — no currency symbols, spaces, or commas.
- Keep product descriptions exactly as printed. Do not add batch/lot/expiry/V-code info to description.
- Use empty string for any field not visible on the invoice.
- Do not guess or invent values."""


def extract_purchase_rows_with_openai(content: bytes, ext: str) -> list[dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return []
    parts = openai_purchase_content_parts(content, ext)
    if not parts:
        return []
    parts.append({"type": "text", "text": OPENAI_PURCHASE_EXTRACTION_PROMPT})
    try:
        data = call_openai_invoice_extractor(api_key, parts)
    except Exception as exc:
        raise RuntimeError(f"OpenAI extraction failed: {exc}") from exc
    return openai_invoice_to_purchase_rows(data)


def openai_purchase_content_parts(content: bytes, ext: str) -> list[dict[str, Any]]:
    ext = (ext or "").lower().lstrip(".")
    if ext in PURCHASE_IMAGE_EXTENSIONS:
        parts = openai_image_parts_with_pillow(content, ext)
        if parts:
            return parts
        mime = OPENAI_DIRECT_IMAGE_MIME.get(ext)
        if mime:
            return [openai_image_part(content, mime)]
        return []

    if ext == "pdf":
        text = extract_pdf_text_with_pdfplumber(content) or extract_pdf_text(content)
        if text and len(text.strip()) > 100:
            return [{"type": "text", "text": f"Invoice text content:\n\n{text}"}]
        return openai_pdf_page_image_parts(content)
    return []


def openai_image_part(content: bytes, mime: str = "image/png") -> dict[str, Any]:
    encoded = base64.b64encode(content).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": "high"}}


def openai_image_parts_with_pillow(content: bytes, ext: str) -> list[dict[str, Any]]:
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except Exception:
        return []
    try:
        with Image.open(io.BytesIO(content)) as image:
            frame = image.copy().convert("RGB")
            output = io.BytesIO()
            frame.save(output, format="PNG")
            return [openai_image_part(output.getvalue(), "image/png")]
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
        command = [pdftoppm, "-png", "-r", "200", "-f", "1"]
        if max_pages > 0:
            command.extend(["-l", str(max_pages)])
        command.extend([str(pdf_path), str(output_prefix)])
        result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
        if result.returncode != 0:
            return []
        return [openai_image_part(path.read_bytes(), "image/png") for path in sorted(Path(tmp).glob("page-*.png"))]


def openai_purchase_pdf_max_pages() -> int:
    try:
        return max(0, int(os.environ.get("OPENAI_PURCHASE_PDF_MAX_PAGES", "10")))
    except ValueError:
        return 10


def call_openai_invoice_extractor(api_key: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    model = os.environ.get("OPENAI_PURCHASE_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": parts}],
        "max_tokens": 8192,
        "temperature": 0,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    raw = str(result["choices"][0]["message"]["content"] or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


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
        raw_qty = decimal_value(item.get("qty"))
        raw_unit_price = decimal_value(first_present_raw(item, "unit_price", "rate", "price", "unit_cost"))
        raw_line_amount = decimal_value(
            item.get("line_total_excl_vat") or item.get("line_total_before_vat")
            or item.get("line_total") or item.get("line_subtotal")
        )
        # Use per-line VAT from AI if available, otherwise distribute invoice VAT proportionally
        line_vat_explicit = decimal_value(item.get("line_vat_amount"))
        if line_vat_explicit:
            vat = line_vat_explicit
        elif vat_total:
            if net_sum:
                vat = vat_total * (line_net / net_sum)
            elif index == 0:
                vat = vat_total
        else:
            vat = Decimal("0")
        unit_cost = openai_unit_price(item, line_net)
        rows.append(normalize_purchase_row({
            "invoice_no": data.get("invoice_number") or "",
            "date": data.get("invoice_date") or "",
            "supplier": data.get("supplier") or "",
            "supplier_trn": data.get("trn_vat") or "",
            "bill_to": data.get("bill_to") or "",
            "currency": data.get("currency") or "AED",
            "product": product,
            "sku": str(item.get("sku") or "").strip(),
            "quantity": decimal_value(item.get("qty")) or 1,
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
    return first_decimal_value(
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


def extract_pdf_text(content: bytes) -> str:
    chunks: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", content, flags=re.DOTALL):
        stream = match.group(1)
        header = content[max(0, match.start() - 300):match.start()]
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
    for array in re.findall(r"\[(.*?)\]\s*TJ", data, flags=re.DOTALL):
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
        qty_match = re.search(r"(?:^|\s)([0-9]+(?:\.[0-9]+)?)\s*(?:pcs|nos|qty|each|ea|unit|units|kg|ltr|mtr)?\b", line, flags=re.IGNORECASE)
        description = re.sub(r"\b[0-9][0-9,]*(?:\.[0-9]{1,2})?\b", " ", line)
        description = re.sub(r"\b(?:AED|VAT|TOTAL|SUBTOTAL|TAX|QTY|PCS|NOS)\b", " ", description, flags=re.IGNORECASE)
        description = re.sub(r"\s+", " ", description).strip(" :-")
        if pending_description and (not description or len(description) < 4 or description.replace(".", "").isdigit()):
            description = pending_description
        if not description or re.search(r"\b(total|subtotal|vat|tax|amount due|balance|invoice|date|trn)\b", description, flags=re.IGNORECASE):
            continue
        qty = decimal_value(qty_match.group(1) if qty_match else 1) or Decimal("1")
        line_total = decimal_value(money[-1])
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
    current_user: User,
    rows: list[dict[str, Any]],
    filename: str,
) -> list[dict[str, Any]]:
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
        upsert_purchase_master_data(db, current_user, category, unit, sku, product, supplier, unit_cost)

        invoice = grouped.setdefault(
            invoice_no,
            {
                "invoice_no": invoice_no,
                "date": excel_date_value(row.get("date")),
                "supplier": supplier,
                "bill_to": str(row.get("bill_to") or ""),
                "currency": str(row.get("currency") or "AED"),
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
                "confidence": 92,
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
                "discount_percent": float(discount_percent),
                "discount_amount": float(decimal_value(row.get("discount_amount"))),
                "unit_cost_before_tax": float(unit_cost_before_tax),
                "line_total": float(line_total),
                "profit_margin": float(decimal_value(row.get("profit_margin"))),
                "selling_price_inc_tax": float(decimal_value(row.get("selling_price_inc_tax"))),
                "raw": row.get("raw") or {},
            }
        )
    invoices = []
    for invoice in grouped.values():
        discount_type = str(invoice.get("discount_type") or "None")
        discount_value = decimal_value(invoice.get("discount_value"))
        discount = invoice["subtotal"] * (discount_value / Decimal("100")) if discount_type == "Percentage" else discount_value if discount_type == "Fixed" else Decimal("0")
        taxable = max(Decimal("0"), invoice["subtotal"] - discount)
        if not invoice["vat_amount"] and "5%" in str(invoice.get("tax_type") or "") and "exempt" not in str(invoice.get("tax_type") or "").lower():
            invoice["vat_amount"] = taxable * Decimal("0.05")
        invoice["total"] = taxable + invoice["vat_amount"] + decimal_value(invoice.get("shipping"))
        invoice["due"] = max(Decimal("0"), invoice["total"] - decimal_value(invoice.get("paid")))
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
    current_user: User,
    category: str,
    unit: str,
    sku: str,
    product: str,
    supplier: str,
    cost: Decimal,
) -> None:
    save_app_record(db, current_user, "salesCategories", {"name": category, "type": "Purchase", "status": "Active"})
    save_app_record(db, current_user, "salesUnits", {"code": unit, "name": unit, "status": "Active"})
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
    saved = save_app_record(db, current_user, "products", product_record)
    sync_domain_model(db, current_user, "products", serialize(saved))


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
