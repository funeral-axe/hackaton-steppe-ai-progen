from __future__ import annotations

from datetime import datetime
from enum import Enum
import uuid

import bcrypt
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, create_engine, text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from pgvector.sqlalchemy import Vector

DATABASE_URL = "postgresql+psycopg2://voice_user:voice_password@localhost:5434/voice_db"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)
Base = declarative_base()


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    USER = "user"


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    login = Column(String(100), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    role = Column(String(30), nullable=False, default=UserRole.USER.value)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    allowed_ips = relationship("AllowedIP", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("SessionLog", back_populates="user", cascade="all, delete-orphan")
    login_logs = relationship("LoginLog", back_populates="user")
    audit_logs = relationship("AuditLog", back_populates="user")


class AllowedIP(Base):
    __tablename__ = "allowed_ips"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ip_address = Column(String(50), nullable=False)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    user = relationship("User", back_populates="allowed_ips")


class LoginLog(Base):
    __tablename__ = "login_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    login = Column(String(100), nullable=False)
    ip_address = Column(String(50), nullable=False)
    browser = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False)
    message = Column(Text, nullable=True)
    login_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    user = relationship("User", back_populates="login_logs")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(255), nullable=False)
    object_type = Column(String(100), nullable=True)
    object_name = Column(String(255), nullable=True)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    user = relationship("User", back_populates="audit_logs")


class SessionLog(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_token = Column(String(100), unique=True, index=True, nullable=False, default=lambda: str(uuid.uuid4()))
    ip_address = Column(String(50), nullable=False)
    browser = Column(String(255), nullable=True)
    login_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_activity = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)
    user = relationship("User", back_populates="sessions")


class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)


class AudioFile(Base):
    __tablename__ = "audio_files"
    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String(500), nullable=False)
    file_path = Column(String(2000), unique=True, nullable=False, index=True)
    file_size = Column(Integer, nullable=True)
    modified_timestamp = Column(Float, nullable=True)
    transcription = Column(Text, nullable=True)
    language = Column(String(30), nullable=True)
    duration = Column(Float, nullable=True)
    status = Column(String(30), nullable=False, default="pending", index=True)
    error_message = Column(Text, nullable=True)
    indexed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    segments = relationship(
        "AudioSegment", back_populates="audio_file",
        cascade="all, delete-orphan", order_by="AudioSegment.start_time"
    )


class AudioSegment(Base):
    __tablename__ = "audio_segments"
    id = Column(Integer, primary_key=True, index=True)
    audio_file_id = Column(Integer, ForeignKey("audio_files.id", ondelete="CASCADE"), nullable=False, index=True)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    text = Column(Text, nullable=False)
    audio_file = relationship("AudioFile", back_populates="segments")


class IndexJob(Base):
    __tablename__ = "index_jobs"
    id = Column(Integer, primary_key=True)
    folder_path = Column(String(2000), nullable=False)
    status = Column(String(30), nullable=False, default="queued")
    total_files = Column(Integer, nullable=False, default=0)
    processed_files = Column(Integer, nullable=False, default=0)
    successful_files = Column(Integer, nullable=False, default=0)
    failed_files = Column(Integer, nullable=False, default=0)
    current_file = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)



class VoicePrint(Base):
    __tablename__ = "voiceprints"

    id = Column(Integer, primary_key=True, index=True)
    un = Column(String, nullable=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    middle_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    audio_path = Column(String, nullable=False)
    voiceprint = Column(Vector(192), nullable=False)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)



def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_tables() -> None:
    with engine.begin() as connection:
        connection.execute(
            text("CREATE EXTENSION IF NOT EXISTS vector")
        )

    Base.metadata.create_all(bind=engine)
