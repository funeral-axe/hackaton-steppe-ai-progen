from fastapi import FastAPI
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base
from sqladmin import Admin, ModelView

app = FastAPI(title="Admin Panel Project")
engine = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String)


Base.metadata.create_all(bind=engine)

admin = Admin(app, engine)


class UserAdmin(ModelView, model=User):
    column_list = [User.id, User.username]


admin.add_view(UserAdmin)
