from fastapi import FastAPI

from app.database import Base
from app.database import engine

import app.models


Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="FastAPI Admin"
)


@app.get("/")
def root():
    return {
        "message": "FastAPI работает"
    }