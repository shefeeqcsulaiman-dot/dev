"""Checks on customers and suppliers before they're saved (step B of moving them out of JSON).

A record typed into a form (one save: the Customers / Vendors forms, POS "Add customer",
the row editor) is refused with a clear message when it has no name, a TRN that isn't 15
digits, an email that isn't one, or the name or TRN of another customer / supplier of the
company. A bulk save (AI purchase uploads, CSV imports, restores) never fails over one bad
field: the field is cleaned or dropped and the record still saves, so a batch of 500
suppliers isn't lost to one misread TRN. Duplicates are found through the typed parties
table (app/parties.py), so the company's records aren't decoded.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Party
from app.parties import KINDS

PARTY_COLLECTIONS = frozenset(KINDS)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LABEL = {"customers": "customer", "vendors": "supplier"}


def _name(record: dict[str, Any]) -> str:
    return str(record.get("name") or "").strip()


def _clean(record: dict[str, Any]) -> list[str]:
    """Normalise in place (trimmed email, TRN digits only); return the problems. The name is
    checked trimmed but saved as given: it is the record's key, and an edit or delete sends
    it back exactly as stored."""
    problems = []
    if not _name(record):
        problems.append("Name is required.")
    if record.get("trn") not in (None, ""):
        record["trn"] = "".join(ch for ch in str(record["trn"]) if ch.isdigit())
        if record["trn"] and len(record["trn"]) != 15:
            problems.append("TRN must be 15 digits.")
    if record.get("email") not in (None, ""):
        record["email"] = str(record["email"]).strip()
        if not _EMAIL.match(record["email"]):
            problems.append(f"\"{record['email']}\" is not an email address.")
    return problems


def _others(db: Session, company_id: str, collection: str, key: str):
    """This company's other customers / suppliers: not the record being saved, which is keyed
    by its name (parties.record_key keeps it trimmed and clipped, app/parties.py)."""
    return db.query(Party).filter(Party.company_id == company_id, Party.kind == KINDS[collection],
                                  func.coalesce(Party.record_key, "") != key.strip()[:160])


def check_party_form(db: Session, company_id: str, collection: str, record: dict[str, Any]) -> None:
    """One save from a form: normalise, or refuse with 422 (bad field) / 409 (duplicate)."""
    if collection not in PARTY_COLLECTIONS:
        return
    problems = _clean(record)
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))
    label, key = _LABEL[collection], str(record["name"])
    same_name = _others(db, company_id, collection, key).filter(Party.name_key == _name(record).lower()).first()
    if same_name:
        raise HTTPException(status_code=409, detail=f"A {label} named \"{same_name.name}\" already exists.")
    if record.get("trn"):
        same_trn = _others(db, company_id, collection, key).filter(Party.trn == record["trn"]).first()
        if same_trn:
            raise HTTPException(status_code=409,
                                detail=f"TRN {record['trn']} already belongs to {label} \"{same_trn.name}\".")


def clean_party_batch(db: Session, company_id: str, collection: str, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """A bulk save: bad fields are dropped, nameless records and duplicates of another
    customer / supplier (same name in any case, or a different name with the same TRN keeps
    its TRN dropped) are skipped or cleaned. Returns the records to save and counts."""
    if collection not in PARTY_COLLECTIONS:
        return records, {}
    by_name = {key: name for key, name in db.query(Party.name_key, Party.name).filter(
        Party.company_id == company_id, Party.kind == KINDS[collection])}
    trn_owner = {trn: key for trn, key in db.query(Party.trn, Party.name_key).filter(
        Party.company_id == company_id, Party.kind == KINDS[collection], Party.trn.isnot(None))}
    kept, counts = [], {"skipped": 0, "cleaned": 0}
    for record in records:
        problems = _clean(record)
        name = _name(record)
        if not name:
            counts["skipped"] += 1
            continue
        key = name.lower()
        if key in by_name and by_name[key] != name:
            counts["skipped"] += 1  # "ACME LLC" when "Acme LLC" exists: not a second supplier
            continue
        if problems:
            if record.get("trn") and len(record["trn"]) != 15:
                record["trn"] = ""
            if record.get("email") and not _EMAIL.match(record["email"]):
                record["email"] = ""
            counts["cleaned"] += 1
        if record.get("trn") and trn_owner.get(record["trn"], key) != key:
            record["trn"] = ""
            counts["cleaned"] += 1
        by_name[key] = name
        if record.get("trn"):
            trn_owner[record["trn"]] = key
        kept.append(record)
    return kept, counts
