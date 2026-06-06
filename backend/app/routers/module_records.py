import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import AppDataRecord, User
from app.schemas import AppRecordIn, AppRecordOut


COLLECTIONS = {
    "purchases": "purchaseRecords",
    "items": "products",
    "units": "salesUnits",
    "settings": "settings",
}

router = APIRouter(tags=["module records"])


def _record_key(collection: str, payload: dict[str, Any], fallback: str | None) -> str | None:
    if fallback:
        return fallback
    key_fields = {
        "purchaseRecords": ("ref", "invoice_no", "reference"),
        "products": ("code", "sku", "name"),
        "salesUnits": ("code", "unit_code", "name"),
        "settings": ("key", "name"),
    }
    for field in key_fields.get(collection, ()):
        value = payload.get(field)
        if value:
            return str(value)
    return None


def _out(row: AppDataRecord) -> AppRecordOut:
    return AppRecordOut(
        id=row.id,
        record_key=row.record_key,
        payload=json.loads(row.payload),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_records(name: str, db: Session, current_user: User) -> list[AppRecordOut]:
    collection = COLLECTIONS[name]
    rows = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == current_user.company_id, AppDataRecord.collection == collection)
        .order_by(AppDataRecord.updated_at.desc())
        .all()
    )
    return [_out(row) for row in rows]


def create_record(name: str, payload: AppRecordIn, db: Session, current_user: User) -> AppRecordOut:
    collection = COLLECTIONS[name]
    data = payload.payload
    key = _record_key(collection, data, payload.record_key)
    row = None
    if key:
        row = (
            db.query(AppDataRecord)
            .filter(
                AppDataRecord.company_id == current_user.company_id,
                AppDataRecord.collection == collection,
                AppDataRecord.record_key == key,
            )
            .first()
        )
    if not row:
        row = AppDataRecord(company_id=current_user.company_id, collection=collection, record_key=key, payload="{}")
        db.add(row)
    row.payload = json.dumps(data)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.get("/purchases", response_model=list[AppRecordOut])
def list_purchases(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[AppRecordOut]:
    return list_records("purchases", db, current_user)


@router.post("/purchases", response_model=AppRecordOut, status_code=201)
def create_purchase(payload: AppRecordIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> AppRecordOut:
    return create_record("purchases", payload, db, current_user)


@router.get("/items", response_model=list[AppRecordOut])
def list_items(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[AppRecordOut]:
    return list_records("items", db, current_user)


@router.post("/items", response_model=AppRecordOut, status_code=201)
def create_item(payload: AppRecordIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> AppRecordOut:
    return create_record("items", payload, db, current_user)


@router.get("/units", response_model=list[AppRecordOut])
def list_units(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[AppRecordOut]:
    return list_records("units", db, current_user)


@router.post("/units", response_model=AppRecordOut, status_code=201)
def create_unit(payload: AppRecordIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> AppRecordOut:
    return create_record("units", payload, db, current_user)


@router.get("/settings", response_model=list[AppRecordOut])
def list_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[AppRecordOut]:
    return list_records("settings", db, current_user)


@router.post("/settings", response_model=AppRecordOut, status_code=201)
def create_setting(payload: AppRecordIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> AppRecordOut:
    return create_record("settings", payload, db, current_user)
