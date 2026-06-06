from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Account, AppDataRecord, AuditLog, ExceptionEvent, Invoice, SourceTransaction, TaxLine, User
from app.schemas import AIAssistRequest, AIExceptionExplainRequest, AIResponse, AITransactionValidationRequest


router = APIRouter(prefix="/ai", tags=["ai assistant"])


ACCOUNT_HINTS = (
    (("rent", "lease"), "4000", "Purchases"),
    (("salary", "payroll", "wps"), "6000", "Salary Expense"),
    (("stock", "inventory", "materials", "goods", "steel", "product"), "1200", "Inventory"),
    (("sale", "revenue", "invoice", "customer"), "3000", "Sales Income"),
    (("bank", "cash", "payment", "receipt"), "1000", "Cash and Bank"),
)


def company_snapshot(db: Session, company_id: str) -> dict[str, int | str]:
    output_vat = db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0)).filter(TaxLine.company_id == company_id, TaxLine.direction == "output").scalar()
    input_vat = db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0)).filter(TaxLine.company_id == company_id, TaxLine.direction == "input").scalar()
    exceptions = db.query(func.count(ExceptionEvent.id)).filter(ExceptionEvent.company_id == company_id, ExceptionEvent.status != "closed").scalar() or 0
    return {
        "invoice_count": db.query(func.count(Invoice.id)).filter(Invoice.company_id == company_id).scalar() or 0,
        "source_transaction_count": db.query(func.count(SourceTransaction.id)).filter(SourceTransaction.company_id == company_id).scalar() or 0,
        "open_exception_count": exceptions,
        "audit_log_count": db.query(func.count(AuditLog.id)).filter(AuditLog.company_id == company_id).scalar() or 0,
        "net_vat": str(Decimal(output_vat or 0) - Decimal(input_vat or 0)),
    }


def account_name(db: Session, company_id: str, code: str) -> str:
    account = db.query(Account).filter(Account.company_id == company_id, Account.code == code).first()
    return account.name if account else "Account mapping required"


def suggest_account(description: str, module: str) -> str:
    text = f"{module} {description}".lower()
    for keywords, code, _label in ACCOUNT_HINTS:
        if any(keyword in text for keyword in keywords):
            return code
    return "4000" if module.lower() in {"purchase", "purchase_bill", "expense"} else "3000"


def vat_issues(subtotal: Decimal, vat: Decimal, treatment: str, supplier_trn: str | None, evidence_present: bool) -> list[str]:
    issues: list[str] = []
    expected_vat = subtotal * Decimal("0.05")
    taxable = treatment.lower() in {"standard", "vat5", "5", "5%"}
    if taxable and subtotal > 0 and abs(vat - expected_vat) > Decimal("1.00"):
        issues.append(f"VAT differs from 5% by more than AED 1.00; expected about AED {expected_vat:.2f}.")
    if vat > 0 and not evidence_present:
        issues.append("Taxable VAT is present but supporting tax evidence is not marked as available.")
    if vat > 0 and (not supplier_trn or len("".join(ch for ch in supplier_trn if ch.isdigit())) != 15):
        issues.append("Input VAT recovery should be reviewed because supplier TRN is missing or not 15 digits.")
    if treatment.lower() in {"zero", "zero-rated", "0%"} and vat != 0:
        issues.append("Zero-rated treatment should carry 0 VAT and separate export/supporting evidence.")
    if treatment.lower() == "exempt" and vat != 0:
        issues.append("Exempt treatment should not carry recoverable VAT.")
    return issues


@router.get("/workbench", response_model=AIResponse)
def workbench(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> AIResponse:
    snapshot = company_snapshot(db, current_user.company_id)
    actions = [
        "Use AI intake to extract invoice fields into draft source transactions only.",
        "Run VAT validation before approval so missing TRNs, evidence, and VAT math stay blocked.",
        "Review AI account suggestions, then approve through the existing posting workflow.",
    ]
    if snapshot["open_exception_count"]:
        actions.insert(0, "Open Exception Center and resolve high severity items before VAT filing.")
    return AIResponse(
        answer="AI assistance is available as a review layer around the existing TaxFlow flow: extract, validate, explain, and suggest; humans still approve posting.",
        confidence=88,
        controls=[
            "AI does not post journals or approve source transactions.",
            "All AI advice is scoped to the signed-in company.",
            "Existing validation, approval, tax line, and audit steps remain authoritative.",
        ],
        suggested_actions=actions,
        context=snapshot,
    )


@router.post("/assist", response_model=AIResponse)
def assist(payload: AIAssistRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> AIResponse:
    q = payload.question.lower()
    snapshot = company_snapshot(db, current_user.company_id)
    controls = [
        "Create drafts first; never direct-post from AI.",
        "Validate account mappings, VAT math, TRN, and evidence before approval.",
        "Keep audit logs for user approvals and corrections.",
    ]
    if any(word in q for word in ("vat", "tax", "trn", "filing")):
        answer = f"VAT readiness should focus on math, TRN, evidence, and source status. Current net VAT from posted tax lines is AED {snapshot['net_vat']}."
        actions = ["Review purchase invoices with VAT but missing supplier TRN.", "Confirm tax evidence before input VAT recovery.", "Use VAT return only after source transactions are approved."]
    elif any(word in q for word in ("exception", "error", "failed", "duplicate")):
        answer = f"The exception queue currently has {snapshot['open_exception_count']} open saved exceptions. AI can explain impact, likely root cause, and next action without changing status."
        actions = ["Open high severity exceptions first.", "Check duplicate references before re-uploading.", "Fix source data, then re-run validation."]
    elif any(word in q for word in ("account", "journal", "mapping", "ledger")):
        answer = "AI can suggest account codes from descriptions, but the existing balanced-journal and source-approval controls remain the posting authority."
        actions = ["Review suggested account codes line by line.", "Confirm journals balance before posting.", "Keep manual overrides visible in audit logs."]
    elif any(word in q for word in ("document", "upload", "ocr", "invoice", "receipt")):
        answer = "Document AI should extract fields into a draft only: party, invoice number, date, lines, subtotal, VAT, total, TRN, and confidence. Posting still waits for review and approval."
        actions = ["Upload the document in Purchases.", "Review extracted fields and confidence.", "Validate VAT and account mappings before approval."]
    else:
        answer = "TaxFlow AI is configured as a safe assistant for document intake, VAT checks, account suggestions, exception explanations, reconciliation hints, and report narratives."
        actions = ["Ask about VAT, exceptions, document intake, account mapping, or reports.", "Use the recommended actions as review prompts, not automatic posting."]
    return AIResponse(answer=answer, confidence=86, controls=controls, suggested_actions=actions, context=snapshot)


@router.post("/validate-transaction", response_model=AIResponse)
def validate_transaction(payload: AITransactionValidationRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> AIResponse:
    subtotal = Decimal("0.00")
    vat = Decimal("0.00")
    suggestions: list[str] = []
    existing_codes = {code for (code,) in db.query(Account.code).filter(Account.company_id == current_user.company_id).all()}
    for line in payload.source.lines:
        amount = line.quantity * line.unit_price
        tax = amount * (line.vat_rate / Decimal("100"))
        subtotal += amount
        vat += tax
        suggested = suggest_account(line.description, payload.source.module)
        account_label = account_name(db, current_user.company_id, suggested)
        if line.account_code not in existing_codes:
            suggestions.append(f"{line.description}: account {line.account_code} is missing; consider {suggested} - {account_label}.")
        elif line.account_code != suggested:
            suggestions.append(f"{line.description}: review account {line.account_code}; AI suggestion is {suggested} - {account_label}.")
    issues = vat_issues(subtotal, vat, payload.tax_treatment, payload.supplier_trn, payload.evidence_present)
    confidence = 92 if not issues and not suggestions else 72 if len(issues) + len(suggestions) <= 2 else 55
    return AIResponse(
        answer="AI transaction review completed. This is a draft review only; approval and posting must use the existing source transaction flow.",
        confidence=confidence,
        controls=["Draft review only.", "Posting blocked until source validation and approval.", "Tax evidence and account mappings must be reviewed by a user."],
        suggested_actions=issues + suggestions or ["No AI review issues found. Continue with normal validation and approval."],
        context={"subtotal": str(subtotal), "vat": str(vat), "total": str(subtotal + vat), "posting_allowed": False},
    )


@router.post("/explain-exception", response_model=AIResponse)
def explain_exception(payload: AIExceptionExplainRequest, current_user: User = Depends(get_current_user)) -> AIResponse:
    del current_user
    text = f"{payload.category} {payload.message}".lower()
    if "duplicate" in text:
        answer = "This looks like a duplicate reference risk. Duplicate invoices can overstate revenue, purchases, VAT, and payables/receivables."
        actions = ["Compare invoice number, party, date, and total.", "Void or merge the duplicate draft.", "Keep one approved source transaction only."]
    elif "trn" in text or "vat" in text:
        answer = "This is a VAT support issue. Input VAT should not be claimed unless the tax invoice evidence and TRN are supportable."
        actions = ["Request corrected tax invoice evidence.", "Mark VAT non-recoverable if support is missing.", "Re-run validation before VAT return."]
    elif "posting" in text or "account" in text or "journal" in text:
        answer = "This is an accounting control issue. Posting should stay blocked until mappings exist and journal lines balance."
        actions = ["Check account mappings.", "Validate source transaction again.", "Approve only after the validation result is clean."]
    elif "iban" in text or "wps" in text or "payroll" in text:
        answer = "This is a payroll/WPS readiness issue. Bank upload should stay blocked for affected employees until required details are complete."
        actions = ["Add missing IBAN/WPS details.", "Regenerate payroll validation.", "Exclude unresolved employees from bank upload."]
    else:
        answer = "This exception should be reviewed before continuing the workflow because it may affect validation, posting, VAT reporting, or audit evidence."
        actions = ["Open the source module.", "Correct the source data.", "Refresh the Exception Center after saving."]
    return AIResponse(
        answer=answer,
        confidence=82,
        controls=["Explanation only; exception status is unchanged.", "Workflow controls remain enforced by backend validation."],
        suggested_actions=actions,
        context={"module": payload.module, "severity": payload.severity, "source_record": payload.source_record or ""},
    )
