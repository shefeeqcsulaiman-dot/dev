from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Document, User
from app.schemas import DocumentOut
from app.storage import upload_fileobj


router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=list[DocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Document]:
    return (
        db.query(Document)
        .filter(Document.company_id == current_user.company_id)
        .order_by(Document.created_at.desc())
        .all()
    )


@router.post("", response_model=DocumentOut, status_code=201)
def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Document:
    storage_key = upload_fileobj(
        current_user.company_id,
        file.filename or "document",
        file.content_type or "application/octet-stream",
        file.file,
    )
    document = Document(
        company_id=current_user.company_id,
        filename=file.filename or "document",
        content_type=file.content_type or "application/octet-stream",
        storage_key=storage_key,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document
