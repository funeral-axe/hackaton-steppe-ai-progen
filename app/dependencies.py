from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

from jose import jwt, JWTError

from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

from app.security import SECRET_KEY, ALGORITHM


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
):

    credentials_exception = HTTPException(
        status_code=401, detail="Не удалось подтвердить пользователя"
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        username = payload.get("sub")

        if username is None:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()

    if user is None:
        raise credentials_exception

    return user


def admin_required(current_user: User = Depends(get_current_user)):

    if current_user.role.name != "admin":
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    return current_user
