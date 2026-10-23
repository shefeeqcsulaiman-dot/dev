"""Support tickets: a company user (main app or HRMS) raises a ticket to the platform team,
and both sides reply in it. Company side: /support/... ; Super Admin side: /superadmin/support/...

A ticket's unread flags say who has a reply they have not opened: unread_support for the
platform team (set when the company writes), unread_company for the company (set when the
team writes). Opening the ticket clears that side's flag."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import Principal, get_current_principal
from app.models import Company, SupportMessage, SupportTicket, User
from app.routers.leave import checked_attachment
from app.routers.superadmin import _require_superadmin

router = APIRouter(tags=["support"])

CATEGORIES = ("Question", "Bug", "Billing", "Feature request", "Other")
PRIORITIES = ("Low", "Normal", "High", "Urgent")
STATUSES = ("open", "in_progress", "resolved", "closed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _ticket_out(t: SupportTicket, company_name: str | None = None, side: str = "company") -> dict:
    return {
        "id": t.id, "number": t.number, "reference": f"T-{t.number:04d}",
        "subject": t.subject, "category": t.category, "priority": t.priority, "status": t.status,
        "raised_by": t.raised_by_name, "raised_by_email": t.raised_by_email if side == "support" else None,
        "company_id": t.company_id if side == "support" else None, "company_name": company_name,
        "created_at": _iso(t.created_at), "last_message_at": _iso(t.last_message_at or t.created_at),
        "unread": bool(t.unread_company if side == "company" else t.unread_support),
    }


def _message_out(m: SupportMessage) -> dict:
    return {"id": m.id, "side": m.side, "author": m.author_name, "body": m.body,
            "attachment_name": m.attachment_name, "has_attachment": bool(m.attachment), "created_at": _iso(m.created_at)}


def _who(principal: Principal) -> tuple[str, str | None]:
    if principal.user:
        return principal.user.full_name or principal.user.email, principal.user.email
    if principal.employee:
        return principal.employee.full_name, None
    return principal.display_name or "Company user", None


def _require_company_user(principal: Principal = Depends(get_current_principal)) -> Principal:
    """Main app and HRMS logins (company admin users and HRMS sub-users). Not the platform's own
    Super Admin (it answers tickets instead) and not shared branch logins."""
    if principal.kind == "branch":
        raise HTTPException(status_code=403, detail="Raise support tickets from a personal login")
    if principal.user and principal.user.role == "superadmin":
        raise HTTPException(status_code=403, detail="Super Admin answers tickets in Super Admin > Support Tickets")
    return principal


class TicketIn(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    category: str = "Question"
    priority: str = "Normal"
    message: str = Field(min_length=1, max_length=8000)
    attachment: str | None = None
    attachment_name: str | None = None

    @field_validator("subject", "message")
    @classmethod
    def _text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This cannot be empty")
        return v

    @field_validator("category")
    @classmethod
    def _cat(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"Category must be one of {', '.join(CATEGORIES)}")
        return v

    @field_validator("priority")
    @classmethod
    def _pri(cls, v: str) -> str:
        if v not in PRIORITIES:
            raise ValueError(f"Priority must be one of {', '.join(PRIORITIES)}")
        return v


class ReplyIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    attachment: str | None = None
    attachment_name: str | None = None

    @field_validator("message")
    @classmethod
    def _text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Write a message")
        return v


def _add_message(db: Session, ticket: SupportTicket, side: str, author: str | None, payload) -> SupportMessage:
    attachment, attachment_name = checked_attachment(payload.attachment, payload.attachment_name)
    msg = SupportMessage(ticket_id=ticket.id, side=side, author_name=author, body=payload.message,
                         attachment=attachment, attachment_name=attachment_name, created_at=_now())
    db.add(msg)
    ticket.last_message_at = msg.created_at
    if side == "company":
        ticket.unread_support = True
        if ticket.status in ("resolved", "closed"):
            ticket.status = "open"            # the company wrote again: the ticket is open again
    else:
        ticket.unread_company = True
    return msg


def _attachment_file(m: SupportMessage):
    import base64
    import re

    from fastapi.responses import Response
    match = re.match(r"^data:([\w/+.-]+);base64,(.*)$", m.attachment or "", re.S)
    if not match:
        raise HTTPException(status_code=404, detail="No attachment")
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", m.attachment_name or "attachment")
    return Response(content=base64.b64decode(match.group(2)), media_type=match.group(1),
                    headers={"Content-Disposition": f'inline; filename="{safe}"', "Cache-Control": "private, no-store"})


# ── Company side ──────────────────────────────────────────────────────────────

def _own_ticket(db: Session, principal: Principal, ticket_id: str) -> SupportTicket:
    t = db.query(SupportTicket).filter(SupportTicket.id == ticket_id, SupportTicket.company_id == principal.company_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t


@router.get("/support/tickets")
def my_tickets(db: Session = Depends(get_db), principal: Principal = Depends(_require_company_user)) -> list[dict]:
    rows = (db.query(SupportTicket).filter(SupportTicket.company_id == principal.company_id)
            .order_by(SupportTicket.last_message_at.desc().nullslast(), SupportTicket.created_at.desc()).limit(200).all())
    return [_ticket_out(t) for t in rows]


@router.get("/support/unread")
def my_unread(db: Session = Depends(get_db), principal: Principal = Depends(_require_company_user)) -> dict:
    n = db.query(func.count(SupportTicket.id)).filter(SupportTicket.company_id == principal.company_id,
                                                      SupportTicket.unread_company.is_(True)).scalar() or 0
    return {"unread": n}


@router.post("/support/tickets", status_code=201)
def raise_ticket(payload: TicketIn, db: Session = Depends(get_db), principal: Principal = Depends(_require_company_user)) -> dict:
    name, email = _who(principal)
    number = (db.query(func.max(SupportTicket.number)).scalar() or 1000) + 1
    t = SupportTicket(company_id=principal.company_id, number=number, subject=payload.subject, category=payload.category,
                      priority=payload.priority, status="open", raised_by_name=name, raised_by_email=email,
                      unread_support=True, unread_company=False)
    db.add(t)
    db.flush()
    _add_message(db, t, "company", name, payload)
    db.commit()
    return _ticket_out(t)


@router.get("/support/tickets/{ticket_id}")
def my_ticket(ticket_id: str, db: Session = Depends(get_db), principal: Principal = Depends(_require_company_user)) -> dict:
    t = _own_ticket(db, principal, ticket_id)
    if t.unread_company:
        t.unread_company = False
        db.commit()
    msgs = db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id).order_by(SupportMessage.created_at).all()
    return {**_ticket_out(t), "messages": [_message_out(m) for m in msgs]}


@router.post("/support/tickets/{ticket_id}/messages", status_code=201)
def my_reply(ticket_id: str, payload: ReplyIn, db: Session = Depends(get_db), principal: Principal = Depends(_require_company_user)) -> dict:
    t = _own_ticket(db, principal, ticket_id)
    m = _add_message(db, t, "company", _who(principal)[0], payload)
    db.commit()
    return _message_out(m)


@router.post("/support/tickets/{ticket_id}/close")
def my_close(ticket_id: str, db: Session = Depends(get_db), principal: Principal = Depends(_require_company_user)) -> dict:
    t = _own_ticket(db, principal, ticket_id)
    t.status = "closed"
    db.commit()
    return _ticket_out(t)


@router.get("/support/messages/{message_id}/attachment")
def my_attachment(message_id: str, db: Session = Depends(get_db), principal: Principal = Depends(_require_company_user)):
    m = db.get(SupportMessage, message_id)
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    _own_ticket(db, principal, m.ticket_id)
    return _attachment_file(m)


# ── Super Admin side ──────────────────────────────────────────────────────────

class StatusIn(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in STATUSES:
            raise ValueError(f"Status must be one of {', '.join(STATUSES)}")
        return v


@router.get("/superadmin/support/tickets")
def all_tickets(
    status: str = Query(default="", description="open / in_progress / resolved / closed, or 'active' for open + in progress"),
    q: str = Query(default=""),
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
) -> list[dict]:
    query = db.query(SupportTicket, Company.name).join(Company, Company.id == SupportTicket.company_id)
    if status == "active":
        query = query.filter(SupportTicket.status.in_(("open", "in_progress")))
    elif status in STATUSES:
        query = query.filter(SupportTicket.status == status)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(SupportTicket.subject.ilike(like), Company.name.ilike(like), SupportTicket.raised_by_name.ilike(like)))
    rows = query.order_by(SupportTicket.unread_support.desc(), SupportTicket.last_message_at.desc().nullslast()).limit(300).all()
    return [_ticket_out(t, name, side="support") for t, name in rows]


@router.get("/superadmin/support/counts")
def ticket_counts(q: str = Query(default=""), db: Session = Depends(get_db), _: User = Depends(_require_superadmin)) -> dict:
    """Tickets per status (and unread per status) for Super Admin's status tabs, with the same
    search as the list, so every tab shows how many it holds before it is opened."""
    query = db.query(SupportTicket.status, SupportTicket.unread_support, func.count(SupportTicket.id)) \
        .join(Company, Company.id == SupportTicket.company_id)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(SupportTicket.subject.ilike(like), Company.name.ilike(like), SupportTicket.raised_by_name.ilike(like)))
    counts = {s: 0 for s in STATUSES}
    unread = {s: 0 for s in STATUSES}
    for status, is_unread, n in query.group_by(SupportTicket.status, SupportTicket.unread_support).all():
        counts[status] = counts.get(status, 0) + n
        if is_unread:
            unread[status] = unread.get(status, 0) + n
    return {"counts": counts, "unread": unread}


@router.get("/superadmin/support/unread")
def support_unread(db: Session = Depends(get_db), _: User = Depends(_require_superadmin)) -> dict:
    n = db.query(func.count(SupportTicket.id)).filter(SupportTicket.unread_support.is_(True)).scalar() or 0
    return {"unread": n}


def _any_ticket(db: Session, ticket_id: str) -> SupportTicket:
    t = db.get(SupportTicket, ticket_id)
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t


@router.get("/superadmin/support/tickets/{ticket_id}")
def support_ticket(ticket_id: str, db: Session = Depends(get_db), _: User = Depends(_require_superadmin)) -> dict:
    t = _any_ticket(db, ticket_id)
    if t.unread_support:
        t.unread_support = False
        db.commit()
    name = db.query(Company.name).filter(Company.id == t.company_id).scalar()
    msgs = db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id).order_by(SupportMessage.created_at).all()
    return {**_ticket_out(t, name, side="support"), "messages": [_message_out(m) for m in msgs]}


@router.post("/superadmin/support/tickets/{ticket_id}/messages", status_code=201)
def support_reply(ticket_id: str, payload: ReplyIn, db: Session = Depends(get_db), superadmin: User = Depends(_require_superadmin)) -> dict:
    t = _any_ticket(db, ticket_id)
    m = _add_message(db, t, "support", "ETaxFlow Support", payload)
    if t.status == "open":
        t.status = "in_progress"
    db.commit()
    return _message_out(m)


@router.post("/superadmin/support/tickets/{ticket_id}/status")
def support_status(ticket_id: str, payload: StatusIn, db: Session = Depends(get_db), _: User = Depends(_require_superadmin)) -> dict:
    t = _any_ticket(db, ticket_id)
    t.status = payload.status
    if payload.status in ("resolved", "closed"):
        t.unread_company = True        # the company sees the outcome
    db.commit()
    name = db.query(Company.name).filter(Company.id == t.company_id).scalar()
    return _ticket_out(t, name, side="support")


@router.get("/superadmin/support/messages/{message_id}/attachment")
def support_attachment(message_id: str, db: Session = Depends(get_db), _: User = Depends(_require_superadmin)):
    m = db.get(SupportMessage, message_id)
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    return _attachment_file(m)
