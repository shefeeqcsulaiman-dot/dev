from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import AuditLog, User
from app.schemas import AuditLogOut


router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/trail", response_model=list[AuditLogOut])
def audit_trail(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .filter(AuditLog.company_id == current_user.company_id)
        .order_by(AuditLog.created_at.desc())
        .limit(200)
        .all()
    )
