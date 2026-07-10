from fastapi import APIRouter, Depends

from app.dependencies import admin_required

from app.models import User


router = APIRouter(prefix="/admin", tags=["Администратор"])


@router.get("/test")
def admin_test(user: User = Depends(admin_required)):

    return {"message": "Вы администратор", "username": user.username}
