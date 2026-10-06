import io
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_company_admin
from app.models import Document, User
from app.schemas import DocumentOut
from app.storage import upload_fileobj


router = APIRouter(prefix="/documents", dependencies=[Depends(require_company_admin)], tags=["documents"])

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
# Business documents only; no HTML/SVG/scripts, which a browser could run if ever served back.
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic", ".gif", ".csv", ".xlsx", ".xls", ".docx", ".doc", ".txt"}


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
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="This file type isn't allowed — upload a PDF, image, spreadsheet, Word or text document")
    content = file.file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (max 20 MB)")
    storage_key = upload_fileobj(
        current_user.company_id,
        file.filename or "document",
        file.content_type or "application/octet-stream",
        io.BytesIO(content),
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
