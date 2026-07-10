from sqlalchemy.orm import Session

from app.models import User
from app.models import Role

from app.security import hash_password


def get_user_by_username(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()


def create_role(db: Session, name: str, description: str):

    role = Role(name=name, description=description)

    db.add(role)
    db.commit()
    db.refresh(role)

    return role


def create_user(db: Session, user):

    db_user = User(
        full_name=user.full_name,
        username=user.username,
        email=user.email,
        hashed_password=hash_password(user.password),
        role_id=user.role_id,
    )

    db.add(db_user)

    db.commit()

    db.refresh(db_user)

    return db_user
