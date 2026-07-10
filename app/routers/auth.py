from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import UserLogin, Token

from app.crud import get_user_by_username

from app.security import verify_password, create_access_token


router = APIRouter(prefix="/auth", tags=["Авторизация"])


@router.post("/login", response_model=Token)
def login(user_data: UserLogin, db: Session = Depends(get_db)):

    user = get_user_by_username(db, user_data.username)

    if not user:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    if not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    token = create_access_token({"sub": user.username, "role": user.role.name})

    return {"access_token": token, "token_type": "bearer"}
