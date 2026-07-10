from sqlalchemy.orm import Session

from app.database import SessionLocal

from app.models import Role
from app.models import User

from app.security import hash_password


def init():

    db: Session = SessionLocal()

    if db.query(Role).count() == 0:
        admin = Role(name="admin", description="Administrator")

        user = Role(name="user", description="User")

        db.add(admin)
        db.add(user)

        db.commit()

    if db.query(User).count() == 0:
        admin_role = db.query(Role).filter(Role.name == "admin").first()

        admin = User(
            full_name="Administrator",
            username="admin",
            email="admin@mail.com",
            hashed_password=hash_password("admin123"),
            role_id=admin_role.id,
        )

        db.add(admin)

        db.commit()

    db.close()
