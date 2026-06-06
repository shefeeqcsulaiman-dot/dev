from __future__ import annotations

from decimal import Decimal

from app.database import Base, SessionLocal, engine
from app.models import (
    Account,
    AuditLog,
    Company,
    ExceptionEvent,
    InventoryValuationLayer,
    Invoice,
    InvoiceLine,
    ItemUnit,
    ItemUnitConversion,
    JournalEntry,
    JournalLine,
    PostingJob,
    SourceTransaction,
    SourceTransactionLine,
    StockMovement,
    StockProductMapping,
    TaxCode,
    TaxLine,
    User,
    Warehouse,
)
from app.security import hash_password


PREFIX = "QA"


def ensure_company(db, name: str, trn: str) -> Company:
    company = db.query(Company).filter(Company.trn == trn).first()
    if not company:
        company = Company(name=name, trn=trn, country="United Arab Emirates")
        db.add(company)
        db.flush()
    company.name = name
    return company


def ensure_user(db, company: Company, email: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(company_id=company.id, email=email, full_name=email.split("@")[0], role="admin")
        db.add(user)
    user.company_id = company.id
    user.role = "admin"
    user.password_hash = hash_password("admin123")
    db.flush()
    return user


def ensure_account(db, company: Company, code: str, name: str, account_type: str) -> Account:
    account = db.query(Account).filter(Account.company_id == company.id, Account.code == code).first()
    if not account:
        account = Account(company_id=company.id, code=code, name=name, type=account_type)
        db.add(account)
    account.name = name
    account.type = account_type
    db.flush()
    return account


def ensure_tax_code(db, company: Company) -> TaxCode:
    code = db.query(TaxCode).filter(TaxCode.company_id == company.id, TaxCode.code == "VAT5").first()
    if not code:
        code = TaxCode(company_id=company.id, code="VAT5", name="Standard UAE VAT", rate=5, recoverable=True, reporting_box="Box 1")
        db.add(code)
    db.flush()
    return code


def clear_existing(db, company: Company) -> None:
    db.query(PostingJob).filter(PostingJob.company_id == company.id).delete(synchronize_session=False)
    db.query(TaxLine).filter(TaxLine.company_id == company.id).delete(synchronize_session=False)
    db.query(StockMovement).filter(StockMovement.company_id == company.id).delete(synchronize_session=False)
    for model, column in (
        (Invoice, Invoice.invoice_number),
        (JournalEntry, JournalEntry.entry_number),
        (SourceTransaction, SourceTransaction.reference),
        (StockProductMapping, StockProductMapping.sku),
        (Warehouse, Warehouse.name),
        (ItemUnit, ItemUnit.item_code),
        (ItemUnitConversion, ItemUnitConversion.item_code),
        (InventoryValuationLayer, InventoryValuationLayer.item_code),
        (ExceptionEvent, ExceptionEvent.source_record),
        (AuditLog, AuditLog.record_id),
    ):
        db.query(model).filter(model.company_id == company.id, column.like(f"{PREFIX}-%")).delete(synchronize_session=False)


def seed_tenant(db, company: Company, user: User) -> None:
    ar = ensure_account(db, company, "1100", "Accounts Receivable", "asset")
    inventory = ensure_account(db, company, "1200", "Inventory", "asset")
    vat_output = ensure_account(db, company, "2200", "VAT Output Payable", "liability")
    sales = ensure_account(db, company, "3000", "Sales Income", "revenue")
    purchases = ensure_account(db, company, "4000", "Purchases", "expense")
    ensure_account(db, company, "5000", "Cost of Goods Sold", "expense")
    tax_code = ensure_tax_code(db, company)

    warehouse = Warehouse(company_id=company.id, name=f"{PREFIX}-Main Store", location="Dubai")
    mapping = StockProductMapping(
        company_id=company.id,
        sku=f"{PREFIX}-COKE",
        name="Coca Cola",
        taxflow_name="Coca Cola",
        units_per_outer=Decimal("12"),
        cost=Decimal("120.00"),
        inventory_account_code=inventory.code,
        purchase_account_code=purchases.code,
        sales_account_code=sales.code,
        tax_code=tax_code.code,
    )
    db.add_all([warehouse, mapping])
    db.flush()

    db.add_all(
        [
            ItemUnit(company_id=company.id, item_code=f"{PREFIX}-COKE", unit_code="PCS", unit_name="Pieces", conversion_factor=1, is_base_unit=True, sales_default=True),
            ItemUnit(company_id=company.id, item_code=f"{PREFIX}-COKE", unit_code="BOX", unit_name="Box", conversion_factor=12, purchase_default=True),
            ItemUnitConversion(company_id=company.id, item_code=f"{PREFIX}-COKE", from_unit_code="BOX", to_unit_code="PCS", conversion_factor=12),
            StockMovement(company_id=company.id, mapping_id=mapping.id, warehouse_id=warehouse.id, movement_type="purchase", quantity=Decimal("12"), unit_cost=Decimal("10.00"), reference=f"{PREFIX}-PUR-001"),
            InventoryValuationLayer(company_id=company.id, item_code=f"{PREFIX}-COKE", warehouse_id=warehouse.id, source_module="purchase", source_id=f"{PREFIX}-PUR-001", quantity_in=Decimal("12"), quantity_remaining=Decimal("10"), unit_cost=Decimal("10.00")),
        ]
    )

    invoice = Invoice(company_id=company.id, customer_name="QA Customer", invoice_number=f"{PREFIX}-INV-001", status="issued", subtotal=Decimal("100.00"), vat=Decimal("5.00"), total=Decimal("105.00"))
    invoice.lines = [InvoiceLine(description="QA Sale", quantity=1, unit_price=Decimal("100.00"), vat_rate=5)]
    db.add(invoice)

    source = SourceTransaction(company_id=company.id, module="sales", reference=f"{PREFIX}-SRC-001", party_name="QA Customer", status="approved", subtotal=Decimal("100.00"), vat=Decimal("5.00"), total=Decimal("105.00"), approved_by=user.id, validation_result="QA seed approved")
    source.lines = [SourceTransactionLine(description="QA Sale", account_code=sales.code, quantity=1, unit_price=Decimal("100.00"), vat_rate=5, amount=Decimal("100.00"), vat_amount=Decimal("5.00"))]
    db.add(source)
    db.flush()

    journal = JournalEntry(company_id=company.id, entry_number=f"{PREFIX}-JE-001", source_module="sales", source_id=source.id, description="QA balanced sales posting")
    journal.lines = [
        JournalLine(account_id=ar.id, description="QA receivable", debit=Decimal("105.00"), credit=0),
        JournalLine(account_id=sales.id, description="QA revenue", debit=0, credit=Decimal("100.00")),
        JournalLine(account_id=vat_output.id, description="QA VAT output", debit=0, credit=Decimal("5.00")),
    ]
    db.add_all(
        [
            journal,
            TaxLine(company_id=company.id, source_id=source.id, tax_code_id=tax_code.id, direction="output", taxable_amount=Decimal("100.00"), tax_amount=Decimal("5.00"), period="2026-05"),
            PostingJob(company_id=company.id, source_id=source.id, status="queued"),
            ExceptionEvent(company_id=company.id, module="Accounting", category="Failed posting", severity="high", source_record=f"{PREFIX}-JOB-FAILED", message="QA deterministic failed posting exception"),
            AuditLog(company_id=company.id, user_id=user.id, module="qa-seed", action="seeded", record_id=f"{PREFIX}-INV-001", detail="Deterministic QA seed data loaded"),
        ]
    )


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        tenants = [
            ("TaxFlow QA Tenant One", "900000000000001", "qa-admin@taxflowqa.com"),
            ("TaxFlow QA Tenant Two", "900000000000002", "qa-other@taxflowqa.com"),
        ]
        for name, trn, email in tenants:
            company = ensure_company(db, name, trn)
            user = ensure_user(db, company, email)
            clear_existing(db, company)
            seed_tenant(db, company, user)
        db.commit()
        print("Seeded deterministic QA tenants and workflow records.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
