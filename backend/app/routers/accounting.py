import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.accounting_posting import create_gl_entries_from_journal, money, post_source_transaction
from app.auth_principal import Principal, require_principal_permission, resolve_active_branch
from app.database import get_db
from app.dependencies import get_current_user, require_module
from app.models import (
    Account,
    AuditLog,
    BankAccount,
    BankReconciliationMatch,
    BankStatementLine,
    Branch,
    GeneralLedgerEntry,
    JournalEntry,
    JournalLine,
    Payment,
    PeriodLock,
    PostingJob,
    Receipt,
    SourceTransaction,
    TaxLine,
    User,
    Voucher,
    VoucherLine,
    VoucherType,
)
from app.schemas import (
    AccountIn,
    AccountOut,
    AccountTreeNode,
    BankAccountCreate,
    BankAccountOut,
    BankMatchCreate,
    BankMatchOut,
    BankStatementLineCreate,
    BankStatementLineOut,
    GeneralLedgerEntryOut,
    JournalCreate,
    JournalOut,
    PaymentCreate,
    PaymentOut,
    PeriodLockIn,
    PeriodLockOut,
    PostingJobOut,
    ReceiptCreate,
    ReceiptOut,
    VoucherCreate,
    VoucherOut,
    VoucherTypeIn,
    VoucherTypeOut,
)


router = APIRouter(tags=["accounting"], dependencies=[Depends(require_module("accounting"))])


def next_number(db: Session, company_id: str, prefix: str, model: object, field: object) -> str:
    total = db.query(func.count(model.id)).filter(model.company_id == company_id).scalar() or 0
    return f"{prefix}-{int(total) + 1:05d}"


def ensure_voucher_type(db: Session, company_id: str, code: str, name: str, prefix: str) -> VoucherType:
    voucher_type = db.query(VoucherType).filter(VoucherType.company_id == company_id, VoucherType.code == code).first()
    if voucher_type:
        return voucher_type
    voucher_type = VoucherType(company_id=company_id, code=code, name=name, prefix=prefix)
    db.add(voucher_type)
    db.flush()
    return voucher_type


def assert_period_open(db: Session, company_id: str, module: str, value: datetime | None) -> None:
    period = (value or datetime.now(timezone.utc)).strftime("%Y-%m")
    lock = (
        db.query(PeriodLock)
        .filter(PeriodLock.company_id == company_id, PeriodLock.module.in_([module, "accounting"]), PeriodLock.period == period, PeriodLock.status == "locked")
        .first()
    )
    if lock:
        raise HTTPException(status_code=423, detail=f"{module.title()} period {period} is locked")


def validate_lines(db: Session, company_id: str, lines: list) -> None:
    debit = sum((line.debit for line in lines), Decimal("0.00"))
    credit = sum((line.credit for line in lines), Decimal("0.00"))
    if money(debit) != money(credit):
        raise HTTPException(status_code=422, detail="Voucher must balance: total debit must equal total credit")
    account_ids = {line.account_id for line in lines}
    found = db.query(Account.id).filter(Account.company_id == company_id, Account.id.in_(account_ids)).all()
    if len(found) != len(account_ids):
        raise HTTPException(status_code=422, detail="One or more voucher accounts do not belong to this company")


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("accounting:view"))) -> list[Account]:
    return db.query(Account).filter(Account.company_id == principal.company_id).order_by(Account.code).all()


@router.get("/accounts/tree", response_model=list[AccountTreeNode])
def list_accounts_tree(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("accounting:view"))) -> list[AccountTreeNode]:
    accounts = db.query(Account).filter(Account.company_id == principal.company_id).order_by(Account.code).all()
    by_id: dict[str, AccountTreeNode] = {}
    roots: list[AccountTreeNode] = []
    for acc in accounts:
        node = AccountTreeNode.model_validate(acc)
        by_id[acc.id] = node
    for acc in accounts:
        node = by_id[acc.id]
        if acc.parent_account_id and acc.parent_account_id in by_id:
            by_id[acc.parent_account_id].children.append(node)
        else:
            roots.append(node)
    return roots


@router.post("/accounts/ai-generate")
def ai_generate_ledger(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Use AI to generate a ledger tree from a natural language description."""
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    # Get existing codes to pass to AI for duplicate avoidance
    existing_codes = [a.code for a in db.query(Account.code).filter(Account.company_id == current_user.company_id).all()]

    ai_result = _call_ledger_ai(prompt, existing_codes)
    if "error" in ai_result:
        raise HTTPException(status_code=422, detail=ai_result["error"])

    # Validate and enrich the result
    validated = _validate_ai_ledger_tree(ai_result.get("tree", []), existing_codes)
    currency = (current_user.company.currency if current_user.company else None) or "AED"
    if currency != "AED":
        def relabel(node):
            if isinstance(node, dict):
                if isinstance(node.get("name"), str):
                    node["name"] = node["name"].replace("(AED)", f"({currency})")
                for child in node.get("children") or []:
                    relabel(child)
        for node in validated:
            relabel(node)
    return {"tree": validated, "summary": ai_result.get("summary", ""), "prompt": prompt}


@router.post("/accounts/ai-approve")
def ai_approve_ledger(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Validate and save an AI-generated ledger tree after human approval."""
    tree = payload.get("tree", [])
    if not tree:
        raise HTTPException(status_code=400, detail="tree is required")

    existing_codes = {a.code for a in db.query(Account).filter(Account.company_id == current_user.company_id).all()}
    created: list[dict] = []
    errors: list[str] = []

    def save_node(node: dict, parent_id: str | None, level: int) -> str | None:
        code = str(node.get("code", "")).strip().upper()
        name = str(node.get("name", "")).strip()
        account_type = str(node.get("type", "Asset")).strip()
        children = node.get("children", [])
        is_group = bool(children) or bool(node.get("is_group"))
        opening_balance = float(node.get("opening_balance", 0) or 0)
        if not code or not name:
            errors.append(f"Node missing code or name: {node}")
            return None
        if code in existing_codes:
            errors.append(f"Duplicate code '{code}' — skipped")
            return None
        account = Account(
            company_id=current_user.company_id,
            code=code,
            name=name,
            type=account_type,
            parent_account_id=parent_id,
            level=level,
            is_group=is_group,
            node_type=_derive_node_type(level, is_group),
            normal_balance=_derive_normal_balance(account_type),
            opening_balance=opening_balance,
            opening_balance_type=node.get("opening_balance_type", "DR"),
            created_mode="ai",
            status="active",
            is_active=True,
        )
        db.add(account)
        db.flush()
        existing_codes.add(code)
        created.append({"code": code, "name": name, "level": level, "is_group": is_group})
        for child in children:
            save_node(child, account.id, level + 1)
        return account.id

    for root_node in tree:
        save_node(root_node, None, 1)

    db.commit()
    return {"created": created, "errors": errors, "total": len(created)}


_LEDGER_AI_PROMPT = """You are a professional UAE/Dubai chartered accountant helping build a chart of accounts.
The user will describe the ledger(s) they need.
You must return ONLY a valid JSON object — no markdown, no code fences.

!! CRITICAL — RESPOND LITERALLY TO WHAT THE USER ASKS !!
- If the user names 1 account/ledger → create EXACTLY 1 ledger node
- If the user names 2 accounts → create EXACTLY 2 ledger nodes
- If the user says "create full chart of accounts" or "all accounts" → create a full structure
- DO NOT add extra accounts, VAT ledgers, sub-groups, or categories the user did not ask for
- A simple name like "Emirates NBD" → one level-4 ledger, NO parent group needed
- Only use UAE context (VAT, WPS, etc.) when the user explicitly requests it

UAE context (apply ONLY when user asks for it):
- Currency: AED | VAT: 5% FTA | Banks: Emirates NBD, FAB, ADCB, Mashreq, DIB, HSBC UAE, RAK Bank, CBD
- Payroll: WPS Salary, Gratuity Provision, ESB | Expenses: DEWA, Salik, Trade License, Visa Fees

Return this structure:
{
  "summary": "one sentence: exactly what was created",
  "tree": [
    {
      "code": "1110",
      "name": "Ledger name",
      "type": "Asset | Liability | Equity | Revenue | Expense",
      "is_group": false,
      "level": 4,
      "nature": "Debit | Credit",
      "posting": true,
      "children": []
    }
  ]
}

For grouped structures (only when user explicitly asks for groups/hierarchy):
{
  "summary": "...",
  "tree": [
    {
      "code": "1000",
      "name": "Group name",
      "type": "Asset",
      "is_group": true,
      "level": 1,
      "nature": "Debit",
      "children": [/* sub-groups and ledgers */]
    }
  ]
}

Rules:
- Maximum 4 levels (Level 1=Primary Group, Level 2=Secondary Group, Level 3=Sub-Group, Level 4=Ledger)
- Level 4: is_group=false, posting=true
- Level 1-3: is_group=true, posting=false
- Account types: Asset=Debit, Liability=Credit, Equity=Credit, Revenue=Credit, Expense=Debit
- Codes: Assets 1xxx, Liabilities 2xxx, Equity 3xxx, Revenue 4xxx, Expenses 5xxx
- Do NOT use codes from the existing_codes list
- MATCH THE COUNT: user asks for N accounts → return exactly N leaf (posting) nodes"""


def _call_ledger_ai(prompt: str, existing_codes: list[str]) -> dict:
    from app.config import get_settings as _get_settings
    _settings = _get_settings()
    api_key = (_settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not api_key:
        # Fallback: rule-based generation when no AI key
        return _rule_based_ledger_generator(prompt, existing_codes)
    content = f"Existing account codes (DO NOT use these): {existing_codes[:50]}\n\nUser requirement: {prompt}"
    request_payload = {
        "model": os.environ.get("OPENAI_LEDGER_MODEL", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": _LEDGER_AI_PROMPT},
            {"role": "user", "content": content},
        ],
        "max_tokens": 3000,
        "temperature": 0.2,
    }
    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        raw = result["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as exc:
        # Was returning str(exc) straight to the client -- fine for the
        # common case (an OpenAI HTTP/network error message), but this is a
        # bare `except Exception`, so it also catches things like a
        # malformed-response KeyError/JSONDecodeError, which could echo back
        # more of the raw exception (potentially internal detail) than
        # intended. Log the real exception server-side, return a generic
        # message to the client either way.
        logging.getLogger(__name__).warning("AI ledger generation failed: %s", exc)
        return {"error": "AI generation failed. Please try again or use the manual entry form."}


def _rule_based_ledger_generator(prompt: str, existing_codes: list[str]) -> dict:
    """Literal rule-based fallback: creates EXACTLY the accounts mentioned in prompt."""
    import re as _re
    existing_set = set(existing_codes)
    _code_counter = [1100]

    def nc(base_hint: int = 0) -> str:
        base = base_hint if base_hint else _code_counter[0]
        code = str(base)
        while code in existing_set:
            base += 10
            code = str(base)
        existing_set.add(code)
        _code_counter[0] = base + 10
        return code

    def led(name: str, t: str, nat: str, code_hint: int = 0) -> dict:
        return {'code': nc(code_hint), 'name': name, 'type': t, 'is_group': False, 'level': 4, 'nature': nat, 'posting': True, 'children': []}

    text = prompt.lower()

    # Split prompt on common separators to identify individual account names
    # e.g. "Emirates NBD and petty cash" → ["Emirates NBD", "petty cash"]
    # e.g. "Emirates NBD, FAB, ADCB" → ["Emirates NBD", "FAB", "ADCB"]
    raw_parts = _re.split(r'\band\b|[,;]', prompt, flags=_re.IGNORECASE)
    parts = [p.strip() for p in raw_parts if p.strip()]

    # UAE name/type mapping for each extracted name
    UAE_BANKS = {'enbd', 'emirates nbd', 'fab', 'first abu dhabi', 'adcb', 'mashreq',
                 'dib', 'dubai islamic', 'hsbc', 'rak bank', 'cbd', 'standard chartered'}

    trees = []
    for part in parts:
        pl = part.lower()
        # Detect account type from keywords
        if any(k in pl for k in ['bank', 'enbd', 'emirates nbd', 'fab', 'adcb', 'mashreq', 'dib', 'hsbc', 'rak bank', 'cbd']):
            name = part if len(part) < 50 else part[:50]
            if 'bank' not in pl:
                name = name + ' - Current Account (AED)'
            trees.append(led(name, 'Asset', 'Debit', 1100))
        elif any(k in pl for k in ['petty cash', 'cash in hand', 'cash']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Asset', 'Debit', 1160))
        elif any(k in pl for k in ['receivable', 'debtor', 'customer']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Asset', 'Debit', 1200))
        elif any(k in pl for k in ['inventory', 'stock']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Asset', 'Debit', 1300))
        elif any(k in pl for k in ['input vat', 'vat recoverable']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Asset', 'Debit', 1400))
        elif any(k in pl for k in ['payable', 'creditor', 'supplier payable']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Liability', 'Credit', 2100))
        elif any(k in pl for k in ['output vat', 'vat payable']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Liability', 'Credit', 2200))
        elif any(k in pl for k in ['loan', 'borrowing']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Liability', 'Credit', 2500))
        elif any(k in pl for k in ['salary', 'payroll', 'wps']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Expense', 'Debit', 5200))
        elif any(k in pl for k in ['rent', 'rental']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Expense', 'Debit', 5300))
        elif any(k in pl for k in ['expense', 'cost', 'fee', 'charge', 'dewa', 'salik', 'license', 'visa', 'travel']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Expense', 'Debit', 5900))
        elif any(k in pl for k in ['sales', 'revenue', 'income', 'service']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Revenue', 'Credit', 4100))
        elif any(k in pl for k in ['capital', 'equity', 'owner']):
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Equity', 'Credit', 3100))
        else:
            # Unknown type — create as generic asset ledger with the exact name given
            name = part if len(part) < 50 else part[:50]
            trees.append(led(name, 'Asset', 'Debit', 1100))

    if not trees:
        # Absolute fallback
        trees = [led('General Ledger Account (AED)', 'Asset', 'Debit', 1100)]

    count = len(trees)
    return {
        'summary': f'Created {count} ledger account{"s" if count != 1 else ""} as requested',
        'tree': trees,
    }


def _validate_ai_ledger_tree(tree: list, existing_codes: list[str]) -> list:
    """Validate and clean AI-generated tree — mark issues, deduplicate codes."""
    seen_codes: set[str] = set(existing_codes)

    def validate_node(node: dict, level: int) -> dict:
        code = str(node.get("code", "")).strip().upper()
        name = str(node.get("name", "")).strip()
        children = [validate_node(c, level + 1) for c in node.get("children", [])]
        issues = []
        if not code:
            issues.append("Missing code")
        elif code in seen_codes:
            issues.append(f"Duplicate code '{code}'")
        else:
            seen_codes.add(code)
        if not name:
            issues.append("Missing name")
        if level > 4:
            issues.append("Exceeds 4-level maximum depth")
        is_group = bool(children) or bool(node.get("is_group"))
        if level == 4 and is_group:
            is_group = False
        return {
            **node,
            "code": code,
            "name": name,
            "level": level,
            "is_group": is_group,
            "posting": not is_group,
            "children": children,
            "issues": issues,
            "valid": len(issues) == 0,
        }

    return [validate_node(n, 1) for n in tree]


def _derive_node_type(level: int, is_group: bool) -> str:
    if not is_group:
        return "POSTING_LEDGER"
    return "MAIN_LEDGER" if level == 1 else "SUB_LEDGER"


def _derive_normal_balance(account_type: str) -> str:
    credit_types = {
        "liability", "equity", "revenue", "income", "direct income",
        "indirect income", "retained earnings", "sundry creditors",
        "duties & taxes", "provisions", "credit",
    }
    return "CR" if account_type.lower().strip() in credit_types else "DR"


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(
    payload: AccountIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Account:
    level = payload.level
    is_group = payload.is_group

    if payload.parent_account_id:
        parent = db.query(Account).filter(
            Account.company_id == current_user.company_id,
            Account.id == payload.parent_account_id,
        ).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent account not found")
        if not parent.is_group:
            raise HTTPException(status_code=400, detail="Cannot add under a Posting Ledger — choose a Group or Sub Ledger as parent.")
        level = (parent.level or 1) + 1
        if level > 4:
            raise HTTPException(status_code=400, detail="Maximum hierarchy depth is 4 (Primary → Secondary → Sub-Group → Ledger)")
        # Level 4 is always a posting ledger
        if level == 4:
            is_group = False
    else:
        level = 1
        is_group = True  # root-level accounts are always groups (MAIN_LEDGER)

    node_type = _derive_node_type(level, is_group)
    normal_balance = _derive_normal_balance(payload.type)

    data = payload.model_dump()
    data.update({"level": level, "is_group": is_group, "node_type": node_type,
                 "normal_balance": normal_balance, "created_mode": "manual", "status": "active"})
    account = Account(company_id=current_user.company_id, **data)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: str,
    payload: AccountIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Account:
    account = db.query(Account).filter(Account.company_id == current_user.company_id, Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    # Never overwrite structural/derived fields on edit. is_active is excluded
    # too — it's set together with `status` via the dedicated
    # /accounts/{id}/status endpoint, and the frontend's edit form always
    # submits is_active=true regardless of the account's real state, which
    # would otherwise silently reactivate a deactivated account on any edit.
    editable = payload.model_dump(exclude={
        "parent_account_id", "level", "is_group",
        "node_type", "created_mode", "status", "ai_confidence", "is_active",
    })
    # Re-derive normal_balance from updated type
    editable["normal_balance"] = _derive_normal_balance(payload.type)
    for field, value in editable.items():
        setattr(account, field, value)
    db.commit()
    db.refresh(account)
    return account


@router.delete("/accounts", status_code=200)
def clear_all_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete all accounts for the company that are not used in journal lines
    and have no child accounts (same rule as the single-account delete)."""
    used_ids = {
        row[0]
        for row in db.query(JournalLine.account_id)
        .join(JournalEntry, JournalLine.journal_id == JournalEntry.id)
        .filter(JournalEntry.company_id == current_user.company_id)
        .distinct()
        .all()
    }
    accounts = db.query(Account).filter(Account.company_id == current_user.company_id).all()
    parent_ids = {acc.parent_account_id for acc in accounts if acc.parent_account_id}
    deleted, skipped = 0, 0
    for acc in accounts:
        if acc.id in used_ids or acc.id in parent_ids:
            skipped += 1
            continue
        db.delete(acc)
        deleted += 1
    db.commit()
    return {"deleted": deleted, "skipped": skipped}


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(
    account_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    account = db.query(Account).filter(Account.company_id == current_user.company_id, Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    children = db.query(func.count(Account.id)).filter(Account.parent_account_id == account.id).scalar() or 0
    if children:
        raise HTTPException(status_code=409, detail="Cannot delete a Group that has child accounts. Delete children first.")
    used = db.query(func.count(JournalLine.id)).filter(JournalLine.account_id == account.id).scalar() or 0
    if used:
        raise HTTPException(status_code=409, detail="Account is used by journal lines")
    db.delete(account)
    db.commit()
    return None


@router.patch("/accounts/{account_id}/opening-balance")
def update_opening_balance(
    account_id: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    account = db.query(Account).filter(Account.company_id == current_user.company_id, Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.is_group:
        raise HTTPException(status_code=400, detail="Opening balance can only be set on Posting Ledger accounts")
    account.opening_balance = float(payload.get("amount", 0) or 0)
    account.opening_balance_type = payload.get("balance_type", "DR").upper()
    db.commit()
    return {"id": account.id, "opening_balance": float(account.opening_balance), "opening_balance_type": account.opening_balance_type}


@router.patch("/accounts/{account_id}/status")
def update_account_status(
    account_id: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    account = db.query(Account).filter(Account.company_id == current_user.company_id, Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    new_status = str(payload.get("status", "active")).lower()
    if new_status not in {"active", "inactive", "draft"}:
        raise HTTPException(status_code=400, detail="status must be active, inactive, or draft")
    account.status = new_status
    account.is_active = new_status == "active"
    db.commit()
    return {"id": account.id, "status": account.status}


@router.get("/journal")
def list_journals(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("accounting:view")),
) -> dict[str, object]:
    # Paginated the same way GET /app-data/records/{collection} already is —
    # previously this returned every journal entry unconditionally, which
    # measured at ~500ms server time alone with ~2000 entries and only gets
    # worse as a company accumulates real transaction history. Callers that
    # genuinely need everything (clearLedgerRecords()) page through with
    # has_more instead of relying on one unbounded response.
    total_query = db.query(func.count(JournalEntry.id)).filter(JournalEntry.company_id == principal.company_id)
    rows_query = (
        db.query(JournalEntry)
        .options(joinedload(JournalEntry.lines))
        .filter(JournalEntry.company_id == principal.company_id)
    )
    # Branch Security Layer: "accounting:view_all_branches" lets a specific
    # branch employee see company-wide entries without being a full admin;
    # otherwise resolve_active_branch() scopes to the caller's own branch
    # (or, for a genuinely multi-branch employee, whichever of their
    # assigned branches they've switched to) — same two-tier composition
    # as trial_balance_rows() (reports.py) and every other branch-aware
    # endpoint. NULL branch_id (legacy/manual entries) stays visible.
    resolved_branch_id = branch_id if principal.can_cross_branch("accounting") else resolve_active_branch(principal, branch_id)
    if resolved_branch_id:
        branch_filter = (JournalEntry.branch_id == resolved_branch_id) | (JournalEntry.branch_id.is_(None))
        total_query = total_query.filter(branch_filter)
        rows_query = rows_query.filter(branch_filter)
    total = total_query.scalar() or 0
    rows = (
        rows_query
        .order_by(JournalEntry.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "records": [JournalOut.model_validate(row).model_dump(mode="json") for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


def _delete_journal_cascade(db: Session, journal: JournalEntry) -> None:
    """Shared cascade for removing a journal entry: its GL rows, unlinking/
    resetting any Voucher back to re-postable, and resetting the originating
    SourceTransaction + PostingJob (plus dropping the TaxLine) so it can be
    re-approved and re-posted rather than left permanently stuck with no
    journal at all — approve_voucher()/retry_posting_job() both no-op once
    status is already "posted". Does not commit — caller's responsibility,
    so a bulk caller (clear_all_journals) can batch many of these into one
    transaction."""
    journal_id = journal.id
    db.query(GeneralLedgerEntry).filter(GeneralLedgerEntry.journal_entry_id == journal_id).delete()
    db.query(Voucher).filter(Voucher.posted_journal_id == journal_id).update(
        {"posted_journal_id": None, "status": "approved"}
    )
    if journal.source_id:
        db.query(SourceTransaction).filter(SourceTransaction.id == journal.source_id).update({"status": "approved"})
        db.query(PostingJob).filter(PostingJob.source_id == journal.source_id).update({"status": "approved"})
        db.query(TaxLine).filter(TaxLine.source_id == journal.source_id).delete(synchronize_session=False)
    db.delete(journal)


@router.delete("/journal/{journal_id}", status_code=204)
def delete_journal(
    journal_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    journal = (
        db.query(JournalEntry)
        .filter(JournalEntry.id == journal_id, JournalEntry.company_id == current_user.company_id)
        .first()
    )
    if not journal:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    # Posted journals are never deleted (see accounting_posting.py's
    # documented invariant — post an equal-and-opposite entry via Reverse
    # Journal instead). "Clear Ledger Records" (POST /journal/clear-all) is
    # a deliberate, separate, explicit bulk-reset action that intentionally
    # bypasses this guard — it is not a loophole in this one.
    if journal.status == "posted":
        raise HTTPException(status_code=400, detail="Posted journal entries cannot be deleted — use Reverse Journal instead")
    _delete_journal_cascade(db, journal)
    db.commit()


@router.post("/journal/clear-all")
def clear_all_journals(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Deliberate bulk-reset action backing the "Clear Ledger Records" UI
    button — deletes every journal entry for the company regardless of
    status, in one server-side operation. Replaces the frontend's previous
    approach of paging through /journal and calling DELETE /journal/{id}
    once per entry, which the posted-status guard above would now block."""
    journals = db.query(JournalEntry).filter(JournalEntry.company_id == current_user.company_id).all()
    for journal in journals:
        _delete_journal_cascade(db, journal)
    db.commit()
    return {"ok": True, "deleted": len(journals)}


@router.get("/voucher-types", response_model=list[VoucherTypeOut])
def list_voucher_types(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("accounting:view"))) -> list[VoucherType]:
    return db.query(VoucherType).filter(VoucherType.company_id == principal.company_id).order_by(VoucherType.code).all()


@router.post("/voucher-types", response_model=VoucherTypeOut, status_code=201)
def create_voucher_type(
    payload: VoucherTypeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoucherType:
    # Upsert by code — the voucher-entry form calls this to ensure its 5
    # built-in types exist before every post, so a plain insert-only
    # endpoint would accumulate a duplicate VoucherType row on every single
    # journal entry ever posted through it.
    existing = (
        db.query(VoucherType)
        .filter(VoucherType.company_id == current_user.company_id, VoucherType.code == payload.code)
        .first()
    )
    if existing:
        return existing
    row = VoucherType(company_id=current_user.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/vouchers", response_model=list[VoucherOut])
def list_vouchers(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("accounting:view"))) -> list[Voucher]:
    return (
        db.query(Voucher)
        .options(joinedload(Voucher.lines))
        .filter(Voucher.company_id == principal.company_id)
        .order_by(Voucher.created_at.desc())
        .all()
    )


@router.post("/vouchers", response_model=VoucherOut, status_code=201)
def create_voucher(
    payload: VoucherCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Voucher:
    voucher_type = (
        db.query(VoucherType)
        .filter(VoucherType.company_id == current_user.company_id, VoucherType.id == payload.voucher_type_id)
        .first()
    )
    if not voucher_type:
        raise HTTPException(status_code=404, detail="Voucher type not found")
    validate_lines(db, current_user.company_id, payload.lines)
    voucher_date = payload.voucher_date or datetime.now(timezone.utc)
    assert_period_open(db, current_user.company_id, "accounting", voucher_date)
    voucher = Voucher(
        company_id=current_user.company_id,
        voucher_type_id=voucher_type.id,
        voucher_no=payload.voucher_no or next_number(db, current_user.company_id, voucher_type.prefix, Voucher, Voucher.voucher_no),
        voucher_date=voucher_date,
        party=payload.party,
        cost_center=payload.cost_center,
        narration=payload.narration,
        status="pending_approval" if voucher_type.approval_required else "approved",
    )
    voucher.lines = [VoucherLine(**line.model_dump()) for line in payload.lines]
    db.add(voucher)
    db.commit()
    db.refresh(voucher)
    return voucher


@router.post("/vouchers/{voucher_id}/approve", response_model=VoucherOut)
def approve_voucher(
    voucher_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Voucher:
    voucher = (
        db.query(Voucher)
        .options(joinedload(Voucher.lines), joinedload(Voucher.voucher_type))
        .filter(Voucher.company_id == current_user.company_id, Voucher.id == voucher_id)
        .first()
    )
    if not voucher:
        raise HTTPException(status_code=404, detail="Voucher not found")
    if voucher.status == "posted":
        return voucher
    assert_period_open(db, current_user.company_id, "accounting", voucher.voucher_date)
    validate_lines(db, current_user.company_id, voucher.lines)
    journal = JournalEntry(
        company_id=current_user.company_id,
        entry_number=voucher.voucher_no,
        source_module="voucher",
        source_id=voucher.id,
        entry_date=voucher.voucher_date,
        description=voucher.narration or f"{voucher.voucher_type.name} {voucher.voucher_no}",
    )
    journal.lines = [
        JournalLine(account_id=line.account_id, description=line.narration, debit=money(line.debit), credit=money(line.credit))
        for line in voucher.lines
    ]
    db.add(journal)
    db.flush()
    create_gl_entries_from_journal(db, journal, voucher.voucher_no, voucher.voucher_type.name, voucher.party, voucher.cost_center)
    voucher.status = "posted"
    voucher.approved_by = current_user.id
    voucher.approved_at = datetime.now(timezone.utc)
    voucher.posted_journal_id = journal.id
    db.add(AuditLog(company_id=current_user.company_id, user_id=current_user.id, module="accounting", action="voucher_posted", record_id=voucher.id, detail=voucher.voucher_no))
    db.commit()
    db.refresh(voucher)
    return voucher


@router.get("/general-ledger", response_model=list[GeneralLedgerEntryOut])
def list_general_ledger(
    account_id: str | None = None,
    branch_id: str | None = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("accounting:view")),
) -> list[GeneralLedgerEntry]:
    query = (
        db.query(GeneralLedgerEntry)
        .filter(GeneralLedgerEntry.company_id == principal.company_id)
    )
    # Same two-tier branch scoping as list_journals() above.
    resolved_branch_id = branch_id if principal.can_cross_branch("accounting") else resolve_active_branch(principal, branch_id)
    if resolved_branch_id:
        query = query.filter(
            (GeneralLedgerEntry.branch_id == resolved_branch_id) | (GeneralLedgerEntry.branch_id.is_(None))
        )
    if account_id:
        query = query.filter(GeneralLedgerEntry.account_id == account_id)
    return (
        query
        .order_by(GeneralLedgerEntry.entry_date.asc(), GeneralLedgerEntry.created_at.asc())
        .offset(skip)
        .limit(min(limit, 2000))
        .all()
    )


@router.get("/vendors", response_model=list[str])
def list_vendors(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("accounting:view")),
) -> list[str]:
    rows = (
        db.query(SourceTransaction.party_name)
        .filter(
            SourceTransaction.company_id == principal.company_id,
            SourceTransaction.party_name.isnot(None),
            SourceTransaction.party_name != "",
            SourceTransaction.module.in_(["purchase", "purchase_bill", "expense", "expenses"]),
        )
        .distinct()
        .order_by(SourceTransaction.party_name)
        .all()
    )
    # Also pull from Payment payee_name
    pay_rows = (
        db.query(Payment.payee_name)
        .filter(
            Payment.company_id == principal.company_id,
            Payment.payee_name.isnot(None),
            Payment.payee_name != "",
        )
        .distinct()
        .all()
    )
    names = sorted({str(r[0]).strip() for r in rows + pay_rows if r[0] and str(r[0]).strip()})
    return names


@router.post("/journal/{journal_id}/reverse", response_model=JournalOut)
def reverse_journal(
    journal_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalEntry:
    original = (
        db.query(JournalEntry)
        .options(joinedload(JournalEntry.lines))
        .filter(JournalEntry.company_id == current_user.company_id, JournalEntry.id == journal_id)
        .first()
    )
    if not original:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    if original.source_module == "reversal":
        raise HTTPException(status_code=400, detail="Cannot reverse a reversal entry")
    already = db.query(JournalEntry).filter(
        JournalEntry.company_id == current_user.company_id,
        JournalEntry.source_module == "reversal",
        JournalEntry.source_id == original.id,
    ).first()
    if already:
        raise HTTPException(status_code=400, detail="This journal entry has already been reversed")
    reversal = JournalEntry(
        company_id=current_user.company_id,
        branch_id=original.branch_id,
        entry_number=f"REV-{original.entry_number}",
        source_module="reversal",
        source_id=original.id,
        entry_date=datetime.now(timezone.utc),
        description=f"Reversal of {original.entry_number}: {original.description}",
        status="posted",
    )
    reversal.lines = [
        JournalLine(
            account_id=l.account_id,
            description=f"Reversal: {l.description or ''}",
            debit=money(l.credit),
            credit=money(l.debit),
        )
        for l in original.lines
    ]
    db.add(reversal)
    db.flush()
    create_gl_entries_from_journal(
        db, reversal,
        voucher_no=f"REV-{original.entry_number}",
        voucher_type="reversal",
    )
    db.add(AuditLog(
        company_id=current_user.company_id,
        user_id=current_user.id,
        module="accounting",
        action="journal_reversed",
        record_id=original.id,
        detail=reversal.entry_number,
    ))
    db.commit()
    db.refresh(reversal)
    return reversal


@router.get("/posting-jobs", response_model=list[PostingJobOut])
def list_posting_jobs(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("accounting:view")),
) -> list[PostingJob]:
    return (
        db.query(PostingJob)
        .filter(PostingJob.company_id == principal.company_id)
        .order_by(PostingJob.created_at.desc())
        .all()
    )


@router.post("/posting-jobs/{job_id}/retry", response_model=PostingJobOut)
def retry_posting_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PostingJob:
    job = db.query(PostingJob).filter(PostingJob.company_id == current_user.company_id, PostingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Posting job not found")
    if job.status == "posted":
        return job
    post_source_transaction(db, job, current_user.id)
    db.commit()
    db.refresh(job)
    return job


@router.get("/payments", response_model=list[PaymentOut])
def list_payments(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("accounting:view"))) -> list[Payment]:
    return db.query(Payment).filter(Payment.company_id == principal.company_id).order_by(Payment.created_at.desc()).all()


@router.post("/payments", response_model=PaymentOut, status_code=201)
def create_payment(
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Payment:
    payment_date = payload.payment_date or datetime.now(timezone.utc)
    assert_period_open(db, current_user.company_id, "accounting", payment_date)
    payment = Payment(
        company_id=current_user.company_id,
        payment_no=payload.payment_no or next_number(db, current_user.company_id, "PAY", Payment, Payment.payment_no),
        payment_date=payment_date,
        payment_mode=payload.payment_mode,
        cash_bank_account_id=payload.cash_bank_account_id,
        debit_account_id=payload.debit_account_id,
        payee_type=payload.payee_type,
        payee_name=payload.payee_name,
        amount=payload.amount,
        reference_no=payload.reference_no,
        narration=payload.narration,
        attachment=payload.attachment,
        status="draft",
    )
    db.add(payment)
    db.flush()
    if payload.post:
        voucher_type = ensure_voucher_type(db, current_user.company_id, "PAY", "Payment Voucher", "PAY")
        voucher = Voucher(
            company_id=current_user.company_id,
            voucher_type_id=voucher_type.id,
            voucher_no=payment.payment_no,
            voucher_date=payment.payment_date,
            party=payment.payee_name,
            narration=payment.narration or payment.reference_no,
            status="pending_approval",
        )
        voucher.lines = [
            VoucherLine(account_id=payment.debit_account_id, debit=money(payment.amount), credit=Decimal("0.00"), party=payment.payee_name, narration=payment.narration),
            VoucherLine(account_id=payment.cash_bank_account_id, debit=Decimal("0.00"), credit=money(payment.amount), party=payment.payee_name, narration=payment.narration),
        ]
        db.add(voucher)
        db.flush()
        payment.voucher_id = voucher.id
        approve_voucher(voucher.id, db, current_user)
        payment.status = "posted"
    db.commit()
    db.refresh(payment)
    return payment


@router.get("/receipts", response_model=list[ReceiptOut])
def list_receipts(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("accounting:view"))) -> list[Receipt]:
    return db.query(Receipt).filter(Receipt.company_id == principal.company_id).order_by(Receipt.created_at.desc()).all()


@router.post("/receipts", response_model=ReceiptOut, status_code=201)
def create_receipt(
    payload: ReceiptCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Receipt:
    receipt_date = payload.receipt_date or datetime.now(timezone.utc)
    assert_period_open(db, current_user.company_id, "accounting", receipt_date)
    receipt = Receipt(
        company_id=current_user.company_id,
        receipt_no=payload.receipt_no or next_number(db, current_user.company_id, "RCT", Receipt, Receipt.receipt_no),
        receipt_date=receipt_date,
        receipt_mode=payload.receipt_mode,
        cash_bank_account_id=payload.cash_bank_account_id,
        credit_account_id=payload.credit_account_id,
        received_from=payload.received_from,
        amount=payload.amount,
        reference_no=payload.reference_no,
        narration=payload.narration,
        attachment=payload.attachment,
        status="draft",
    )
    db.add(receipt)
    db.flush()
    if payload.post:
        voucher_type = ensure_voucher_type(db, current_user.company_id, "RCT", "Receipt Voucher", "RCT")
        voucher = Voucher(
            company_id=current_user.company_id,
            voucher_type_id=voucher_type.id,
            voucher_no=receipt.receipt_no,
            voucher_date=receipt.receipt_date,
            party=receipt.received_from,
            narration=receipt.narration or receipt.reference_no,
            status="pending_approval",
        )
        voucher.lines = [
            VoucherLine(account_id=receipt.cash_bank_account_id, debit=money(receipt.amount), credit=Decimal("0.00"), party=receipt.received_from, narration=receipt.narration),
            VoucherLine(account_id=receipt.credit_account_id, debit=Decimal("0.00"), credit=money(receipt.amount), party=receipt.received_from, narration=receipt.narration),
        ]
        db.add(voucher)
        db.flush()
        receipt.voucher_id = voucher.id
        approve_voucher(voucher.id, db, current_user)
        receipt.status = "posted"
    db.commit()
    db.refresh(receipt)
    return receipt


@router.get("/bank-accounts", response_model=list[BankAccountOut])
def list_bank_accounts(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("accounting:view"))) -> list[BankAccount]:
    return db.query(BankAccount).filter(BankAccount.company_id == principal.company_id).order_by(BankAccount.bank_name).all()


@router.post("/bank-accounts", response_model=BankAccountOut, status_code=201)
def create_bank_account(
    payload: BankAccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BankAccount:
    account = db.query(Account).filter(Account.company_id == current_user.company_id, Account.id == payload.account_id).first()
    if not account:
        raise HTTPException(status_code=422, detail="Bank ledger account does not belong to this company")
    account.is_bank_cash = True
    row = BankAccount(company_id=current_user.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/bank-statement-lines", response_model=list[BankStatementLineOut])
def list_bank_statement_lines(
    bank_account_id: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("accounting:view")),
) -> list[BankStatementLine]:
    query = db.query(BankStatementLine).filter(BankStatementLine.company_id == principal.company_id)
    if bank_account_id:
        query = query.filter(BankStatementLine.bank_account_id == bank_account_id)
    return query.order_by(BankStatementLine.transaction_date.desc()).all()


@router.post("/bank-statement-lines", response_model=BankStatementLineOut, status_code=201)
def create_bank_statement_line(
    payload: BankStatementLineCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BankStatementLine:
    bank = db.query(BankAccount).filter(BankAccount.company_id == current_user.company_id, BankAccount.id == payload.bank_account_id).first()
    if not bank:
        raise HTTPException(status_code=404, detail="Bank account not found")
    row = BankStatementLine(company_id=current_user.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/bank-reconciliation/matches", response_model=list[BankMatchOut])
def list_bank_matches(
    bank_account_id: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("accounting:view")),
) -> list[BankReconciliationMatch]:
    query = db.query(BankReconciliationMatch).filter(BankReconciliationMatch.company_id == principal.company_id)
    if bank_account_id:
        query = query.filter(BankReconciliationMatch.bank_account_id == bank_account_id)
    return query.order_by(BankReconciliationMatch.confirmed_at.desc()).all()


@router.post("/bank-reconciliation/matches", response_model=BankMatchOut, status_code=201)
def match_bank_line(
    payload: BankMatchCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BankReconciliationMatch:
    statement = db.query(BankStatementLine).filter(BankStatementLine.company_id == current_user.company_id, BankStatementLine.id == payload.statement_line_id).first()
    ledger = db.query(GeneralLedgerEntry).filter(GeneralLedgerEntry.company_id == current_user.company_id, GeneralLedgerEntry.id == payload.ledger_entry_id).first()
    if not statement or not ledger:
        raise HTTPException(status_code=404, detail="Statement line or ledger entry not found")
    existing = (
        db.query(BankReconciliationMatch)
        .filter(
            BankReconciliationMatch.company_id == current_user.company_id,
            BankReconciliationMatch.match_status == "matched",
            (BankReconciliationMatch.statement_line_id == statement.id) | (BankReconciliationMatch.ledger_entry_id == ledger.id),
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Statement line or ledger entry is already matched to something else — unmatch it first")
    match = BankReconciliationMatch(
        company_id=current_user.company_id,
        bank_account_id=statement.bank_account_id,
        statement_line_id=statement.id,
        ledger_entry_id=ledger.id,
        match_status="matched",
        match_method=payload.match_method,
        difference=payload.difference,
        confirmed_by=current_user.id,
        confirmed_at=datetime.now(timezone.utc),
    )
    statement.status = "matched"
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


@router.delete("/bank-reconciliation/matches/{match_id}", status_code=204)
def unmatch_bank_line(
    match_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    match = db.query(BankReconciliationMatch).filter(BankReconciliationMatch.company_id == current_user.company_id, BankReconciliationMatch.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    statement = db.query(BankStatementLine).filter(BankStatementLine.id == match.statement_line_id).first()
    if statement:
        statement.status = "unmatched"
    db.delete(match)
    db.commit()


@router.post("/period-locks", response_model=PeriodLockOut, status_code=201)
def upsert_period_lock(
    payload: PeriodLockIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PeriodLock:
    lock = (
        db.query(PeriodLock)
        .filter(PeriodLock.company_id == current_user.company_id, PeriodLock.module == payload.module, PeriodLock.period == payload.period)
        .first()
    )
    if not lock:
        lock = PeriodLock(company_id=current_user.company_id, module=payload.module, period=payload.period)
        db.add(lock)
    lock.status = payload.status
    lock.reason = payload.reason
    lock.locked_by = current_user.id if payload.status == "locked" else None
    lock.locked_at = datetime.now(timezone.utc) if payload.status == "locked" else None
    db.commit()
    db.refresh(lock)
    return lock


@router.post("/journal", response_model=JournalOut, status_code=201)
def create_journal(
    payload: JournalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalEntry:
    debit = sum((line.debit for line in payload.lines), Decimal("0.00"))
    credit = sum((line.credit for line in payload.lines), Decimal("0.00"))
    if money(debit) != money(credit):
        raise HTTPException(status_code=422, detail="Journal must balance: total debit must equal total credit")
    assert_period_open(db, current_user.company_id, "accounting", payload.entry_date)

    account_ids = {line.account_id for line in payload.lines}
    found = (
        db.query(Account.id)
        .filter(Account.company_id == current_user.company_id, Account.id.in_(account_ids))
        .all()
    )
    if len(found) != len(account_ids):
        raise HTTPException(status_code=422, detail="One or more accounts do not belong to this company")

    if payload.branch_id is not None:
        branch = db.query(Branch.id).filter(Branch.id == payload.branch_id, Branch.company_id == current_user.company_id).first()
        if not branch:
            raise HTTPException(status_code=422, detail="Branch does not belong to this company")

    journal_data = {
        "company_id": current_user.company_id,
        "entry_number": payload.entry_number,
        "source_module": payload.source_module,
        "source_id": payload.source_id,
        "branch_id": payload.branch_id,
        "description": payload.description,
    }
    if payload.entry_date is not None:
        journal_data["entry_date"] = payload.entry_date
    journal = JournalEntry(
        **journal_data,
    )
    journal.lines = [JournalLine(**line.model_dump()) for line in payload.lines]
    db.add(journal)
    db.flush()
    create_gl_entries_from_journal(db, journal, payload.entry_number, payload.source_module)
    db.commit()
    db.refresh(journal)
    return journal
