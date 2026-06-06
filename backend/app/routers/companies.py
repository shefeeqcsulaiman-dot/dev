from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models import User
from app.schemas import CompanyOut


router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("/current", response_model=CompanyOut)
def current_company(current_user: User = Depends(get_current_user)):
    return current_user.company
