from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from passlib.context import CryptContext

DATABASE_URL = "sqlite:///./audio_app.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, unique=True, index=True)
    password = Column(String)  # Хэшированный пароль
    role = Column(String, default="user") # 'admin' или 'user'
    created_at = Column(DateTime, default=datetime.utcnow)

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
        new_admin = User(login="admin", password=get_password_hash("admin"), role="admin")
        db.add(new_admin)
        db.commit()
    db.close()

create_default_admin()