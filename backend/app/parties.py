"""Customers and vendors as real rows (the parties table), step A of moving them out of JSON.

Every save, edit and delete of a "customers" or "vendors" app-data record updates its
row in parties (app/record_mirror.py). The JSON record is still the source of truth; the
rows can be checked or rebuilt with `python -m app.parties [--rebuild]`. Next steps
(docs/scaling-plan-10k.md): reads move to this table, then writes, then the JSON copy goes.

Values are normalised on the way in: TRN keeps its digits only, names get a lower-cased
search key, the credit limit is a number (or empty when it isn't one).
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppDataRecord, Party
from app.record_mirror import Record, RecordMirror

KINDS = {"customers": "customer", "vendors": "vendor"}


def _text(value: Any, size: int) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text[:size] or None


def _money(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or abs(number) >= Decimal("1e12"):
        return None
    return number.quantize(Decimal("0.01"))


def party_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    name = _text(doc.get("name") or doc.get("customer") or doc.get("vendor"), 255) or ""
    trn = "".join(ch for ch in str(doc.get("trn") or "") if ch.isdigit())[:40] or None
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "kind": KINDS[collection],
        "record_key": _text(record_key, 160),
        "name": name,
        "name_key": name.lower(),
        "trn": trn,
        "email": _text(doc.get("email"), 255),
        "phone": _text(doc.get("phone") or doc.get("mobile"), 80),
        "address": _text(doc.get("address"), 4000),
        "emirate": _text(doc.get("emirate"), 80),
        "category": _text(doc.get("category"), 120),
        "contact": _text(doc.get("contact") or doc.get("contact_person"), 255),
        "credit_limit": _money(doc.get("credit_limit")) if doc.get("credit_limit") not in (None, "") else None,
        "status": _text(doc.get("status"), 40),
    }]


mirror = RecordMirror("parties", KINDS, Party, Party.id, party_rows).register()


def verify_company(session: Session, company_id: str) -> bool:
    """True when the company's parties rows match what its JSON records produce."""
    have = {r.id: (r.kind, r.name, r.trn, r.email, r.credit_limit, r.record_key)
            for r in session.query(Party).filter(Party.company_id == company_id)}
    want = {}
    for rec in session.execute(select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection,
                                      AppDataRecord.payload, AppDataRecord.branch_id, AppDataRecord.record_key)
                               .where(AppDataRecord.company_id == company_id,
                                      AppDataRecord.collection.in_(KINDS))):
        for row in party_rows(tuple(rec)):
            want[row["id"]] = (row["kind"], row["name"], row["trn"], row["email"], row["credit_limit"], row["record_key"])
    return have == want


def _main() -> None:
    import argparse

    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Check (or rebuild) the parties table against the JSON records.")
    parser.add_argument("--rebuild", action="store_true", help="rebuild companies whose rows differ")
    args = parser.parse_args()
    with SessionLocal() as db:
        companies = [c for (c,) in db.execute(
            select(AppDataRecord.company_id).where(AppDataRecord.collection.in_(KINDS)).distinct())]
        bad = [c for c in companies if not verify_company(db, c)]
        print(f"{len(companies)} companies checked, {len(bad)} differ")
        if args.rebuild and bad:
            for company_id in bad:
                mirror.rebuild_company(db, company_id)
            db.commit()
            print(f"rebuilt {len(bad)}")


if __name__ == "__main__":
    _main()
