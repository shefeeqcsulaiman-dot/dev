import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.limiter import limiter
from app.models import AppDataRecord

router = APIRouter(tags=["invoice-share"])


def _gen_code() -> str:
    return secrets.token_urlsafe(12)


class SharePayload(BaseModel):
    payload: dict


@router.post("/share/invoice")
def create_invoice_share(
    body: SharePayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    code = ""
    for _ in range(10):
        code = _gen_code()
        exists = (
            db.query(AppDataRecord)
            .filter(
                AppDataRecord.collection == "invoice_share",
                AppDataRecord.record_key == code,
            )
            .first()
        )
        if not exists:
            break
    record = AppDataRecord(
        company_id=current_user.company_id,
        collection="invoice_share",
        record_key=code,
        payload=json.dumps(body.payload),
    )
    db.add(record)
    db.commit()
    return {"code": code}


@router.get("/share/invoice/{code}")
@limiter.limit("20/minute")
def get_invoice_share(request: Request, code: str, db: Session = Depends(get_db)):
    record = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.collection == "invoice_share",
            AppDataRecord.record_key == code,
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Invoice link not found or expired")
    return {"payload": json.loads(record.payload)}
