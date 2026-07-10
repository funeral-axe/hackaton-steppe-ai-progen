from fastapi import FastAPI

from app.database import Base
from app.database import engine

import app.models

from app.init_db import init
from app.routers import auth
from app.routers import admin


# Создаем таблицы в базе данных
Base.metadata.create_all(bind=engine)


app = FastAPI(
    title="FastAPI Admin",
    version="1.0.0"
)
app.include_router(auth.router)
app.include_router(admin.router)

# Создаем начальные данные
init()


@app.get("/")
def root():
    return {
        "message": "FastAPI работает"
    }