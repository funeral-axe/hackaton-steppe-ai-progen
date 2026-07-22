from xmlrpc.client import Boolean

from sqlalchemy import (
    Text,
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Boolean,
    ForeignKey,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from passlib.context import CryptContext
from enum import Enum
import uuid
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from passlib.context import CryptContext

DATABASE_URL = "sqlite:///./audio_app.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    USER = "user"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    login = Column(String(100), unique=True, nullable=False)

    password = Column(String(255), nullable=False)

    role = Column(String(30), default=UserRole.USER.value)

    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    last_login = Column(DateTime)

    allowed_ips = relationship(
        "AllowedIP", back_populates="user", cascade="all, delete"
    )

    sessions = relationship("SessionLog", back_populates="user", cascade="all, delete")

    login_logs = relationship("LoginLog", back_populates="user", cascade="all, delete")

    audit_logs = relationship("AuditLog", back_populates="user", cascade="all, delete")


class AllowedIP(Base):
    __tablename__ = "allowed_ips"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("users.id"))

    ip_address = Column(String(50), nullable=False)

    description = Column(String(255))

    user = relationship("User", back_populates="allowed_ips")
    
class LoginLog(Base):
    __tablename__ = "login_logs"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    login = Column(String(100))

    ip_address = Column(String(50))

    browser = Column(String(150))

    operating_system = Column(String(150))

    status = Column(String(20))

    message = Column(Text)

    login_time = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="login_logs")
    
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("users.id"))

    action = Column(String(255))

    object_type = Column(String(100))

    object_name = Column(String(255))

    ip_address = Column(String(50))

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="audit_logs")
    
class SessionLog(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("users.id"))

    session_token = Column(
        String(100),
        unique=True,
        default=lambda: str(uuid.uuid4())
    )

    ip_address = Column(String(50))

    browser = Column(String(200))

    login_time = Column(DateTime, default=datetime.utcnow)

    last_activity = Column(DateTime, default=datetime.utcnow)

    is_active = Column(Boolean, default=True)

    user = relationship("User", back_populates="sessions")
    
class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)

    key = Column(String(100), unique=True)

    value = Column(Text)


# Создание таблиц при запуске
Base.metadata.create_all(bind=engine)


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


# Функция для создания админа по умолчанию
def create_default_admin():
    db = SessionLocal()
    admin = db.query(User).filter(User.login == "admin").first()
    if not admin:
        new_admin = User(
            login="admin", password=get_password_hash("admin"), role="admin"
        )
        db.add(new_admin)
        db.commit()
    db.close()


create_default_admin()
