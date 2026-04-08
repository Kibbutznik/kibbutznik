import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.schemas.user import UserCreate, UserResponse
from kbz.services.user_service import UserService

router = APIRouter()


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db)):
    svc = UserService(db)
    try:
        user = await svc.create(data)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Username already taken")
    return user


@router.get("/by-name/{user_name}", response_model=UserResponse)
async def get_user_by_name(user_name: str, db: AsyncSession = Depends(get_db)):
    svc = UserService(db)
    user = await svc.get_by_username(user_name)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = UserService(db)
    user = await svc.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
