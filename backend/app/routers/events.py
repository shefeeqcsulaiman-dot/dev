import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import DomainEvent, EventOutbox, User
from app.schemas import DomainEventIn, DomainEventOut


router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[DomainEventOut])
def list_events(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[DomainEvent]:
    return (
        db.query(DomainEvent)
        .filter(DomainEvent.company_id == current_user.company_id)
        .order_by(DomainEvent.created_at.desc())
        .all()
    )


@router.post("", response_model=DomainEventOut, status_code=201)
def create_event(
    payload: DomainEventIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DomainEvent:
    event_payload = json.dumps(payload.payload or {})
    event = DomainEvent(
        company_id=current_user.company_id,
        event_name=payload.event_name,
        source_module=payload.source_module,
        source_id=payload.source_id,
        payload=event_payload,
        correlation_id=payload.correlation_id,
    )
    db.add(event)
    db.flush()
    db.add(
        EventOutbox(
            company_id=current_user.company_id,
            event_id=event.id,
            topic=payload.event_name,
            payload=event_payload,
            status="pending",
        )
    )
    db.commit()
    db.refresh(event)
    return event
