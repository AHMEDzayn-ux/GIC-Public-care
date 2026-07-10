"""
Auth routes — operator register / login / me (JWT).
"""

from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.orm import Session

from api.models import LoginRequest, RegisterRequest, LoginResponse, MeResponse
from services import auth_service
from database import get_db
from db_models import User
from auth import require_admin
from config import get_settings
from logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if not settings.allow_registration:
        raise HTTPException(status_code=403, detail="Registration is disabled")
    email = (payload.email or "").strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if auth_service.get_user_by_email(db, email):
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    user = auth_service.create_user(db, email=email, password=payload.password, name=payload.name)
    token = auth_service.create_token(user.id)
    return LoginResponse(token=token, email=user.email, name=user.name)


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = auth_service.create_token(user.id)
    return LoginResponse(token=token, email=user.email, name=user.name)


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(require_admin)):
    return MeResponse(id=user.id, email=user.email, name=user.name, is_superadmin=user.is_superadmin)
