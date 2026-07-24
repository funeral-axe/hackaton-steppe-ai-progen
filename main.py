from __future__ import annotations
import token

# from django.contrib.gis import db
# from requests import request, request, session

from contextlib import asynccontextmanager
from ipaddress import ip_address
from fastapi.staticfiles import StaticFiles
from typing import Optional
from fastapi import FastAPI, Depends, Request, Form, Response, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from audio_processor import process_audio_files
from datetime import datetime
from datetime import datetime
from uuid import uuid4
from database import (
    AllowedIP,
    AuditLog,
    LoginLog,
    SessionLocal,
    Setting,
    SessionLog,
    User,
    UserRole,
    create_tables,
    get_password_hash,
    verify_password,
)


app = FastAPI()
templates = Jinja2Templates(directory="templates")


# Зависимость для получения сессии БД
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
def valid_ip(value: str) -> bool:
    try:
        ip_address(value)
        return True
    except ValueError:
        return False

def is_ip_allowed(user: User, ip: str) -> bool:
    if not user.allowed_ips:
        return True
    return any(item.ip_address == ip for item in user.allowed_ips)

def write_login_log(
    db: Session,
    request: Request,
    login: str,
    status: str,
    message: str,
    user: Optional[User] = None,
) -> None:
    db.add(
        LoginLog(
            user_id=user.id if user else None,
            login=login,
            ip_address=client_ip(request),
            browser=request.headers.get("user-agent", ""),
            status=status,
            message=message,
        )
    )
    db.commit()
# Функция для записи действий
def write_audit_log(
    db: Session,
    request: Request,
    actor: User,
    action: str,
    object_type: str = "",
    object_name: str = "",
) -> None:
    db.add(
        AuditLog(
            user_id=actor.id,
            action=action,
            object_type=object_type,
            object_name=object_name,
            ip_address=client_ip(request),
        )
    )
    db.commit()


# Зависимость для получения текущего пользователя из куки
def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    token = request.cookies.get("session_token")
    if not token:
        return None

    session = (
        db.query(SessionLog)
        .filter(
            SessionLog.session_token == token,
            SessionLog.is_active.is_(True),
        )
        .first()
    )
    if not session or not session.user or not session.user.is_active:
        return None

    session.last_activity = datetime.utcnow()
    db.commit()
    return session.user

def admin_required(user: Optional[User]) -> bool:
    return bool(user and user.role == UserRole.ADMIN.value)

@asynccontextmanager
async def lifespan(_: FastAPI):
    create_tables()
    #create_default_admin()
    yield


app = FastAPI(title="Sonar", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/login")
async def login_page(request: Request):
    # Исправленный синтаксис TemplateResponse
    return templates.TemplateResponse(request=request, name="login.html",context={"error": request.query_params.get("error")},)


@app.post("/login")
async def login_post(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.login == login.strip()).first()

    if not user or not verify_password(password, user.password):
        write_login_log(db, request, login, "error", "Неверный логин или пароль", user)
        return RedirectResponse("/login?error=credentials", status_code=303)

    if not user.is_active:
        write_login_log(db, request, login, "error", "Пользователь заблокирован", user)
        return RedirectResponse("/login?error=blocked", status_code=303)

    ip = client_ip(request)
    if not is_ip_allowed(user, ip):
        write_login_log(db, request, login, "error", "Вход с запрещенного IP", user)
        return RedirectResponse("/login?error=ip", status_code=303)

    session = SessionLog(
        user_id=user.id,
        ip_address=ip,
        browser=request.headers.get("user-agent", ""),
        is_active=True,
    )
    user.last_login = datetime.utcnow()
    db.add(session)
    db.commit()
    db.refresh(session)

    write_login_log(db, request, login, "success", "Успешный вход", user)

    target = "/admin" if user.role == UserRole.ADMIN.value else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        key="session_token",
        value=session.session_token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return response


@app.get("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("session_token")
    if token:
        session = (
            db.query(SessionLog)
            .filter(SessionLog.session_token == token)
            .first()
        )
        if session:
            session.is_active = False
            db.commit()

    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session_token")
    return response


@app.get("/")
async def index_page(
    request: Request,
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.role == UserRole.ADMIN.value:
        return RedirectResponse("/admin", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": user, "message": request.query_params.get("message")},
    )


@app.post("/search")
async def start_search(
    background_tasks: BackgroundTasks,
    search_mode: str = Form(...),
    query: str = Form(...),
    source_folder: str = Form(...),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    background_tasks.add_task(
        process_audio_files,
        search_mode,
        query,
        source_folder,
    )
    return RedirectResponse("/?message=Поиск запущен в фоне", status_code=303)


@app.get("/admin")
async def admin_dashboard(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    users = db.query(User).order_by(User.id.desc()).all()
    login_logs = db.query(LoginLog).order_by(LoginLog.login_time.desc()).limit(8).all()
    audit_logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(8).all()
    active_sessions = (
        db.query(SessionLog).filter(SessionLog.is_active.is_(True)).count()
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "user": current_user,
            "users_count": len(users),
            "active_users": sum(1 for item in users if item.is_active),
            "admins_count": sum(1 for item in users if item.role == "admin"),
            "active_sessions": active_sessions,
            "login_logs": login_logs,
            "audit_logs": audit_logs,
            "active_page": "dashboard",
        },
    )


@app.get("/admin/users")
async def admin_users(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    users = db.query(User).order_by(User.id.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/users.html",
        context={
            "user": current_user,
            "users": users,
            "active_page": "users",
        },
    )


@app.post("/admin/users/add")
async def add_user(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    login = login.strip()
    if not db.query(User).filter(User.login == login).first():
        new_user = User(
            login=login,
            password=get_password_hash(password),
            role=role if role in {"admin", "operator", "user"} else "user",
            is_active=True,
        )
        db.add(new_user)
        db.commit()
        write_audit_log(db, request, current_user, "Создал пользователя", "User", login)

    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/edit")
async def edit_user(
    request: Request,
    user_id: int = Form(...),
    login: str = Form(...),
    role: str = Form(...),
    is_active: Optional[str] = Form(None),
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    target = db.query(User).filter(User.id == user_id).first()
    if target:
        target.login = login.strip()
        target.role = role if role in {"admin", "operator", "user"} else "user"
        target.is_active = is_active == "on"
        db.commit()
        write_audit_log(
            db, request, current_user, "Изменил пользователя", "User", target.login
        )

    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/password")
async def change_password(
    request: Request,
    user_id: int = Form(...),
    password: str = Form(...),
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    target = db.query(User).filter(User.id == user_id).first()
    if target and len(password) >= 6:
        target.password = get_password_hash(password)
        db.commit()
        write_audit_log(
            db, request, current_user, "Изменил пароль", "User", target.login
        )
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/toggle")
async def toggle_user(
    request: Request,
    user_id: int = Form(...),
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    target = db.query(User).filter(User.id == user_id).first()
    if target and target.id != current_user.id:
        target.is_active = not target.is_active
        db.commit()
        write_audit_log(
            db, request, current_user, "Изменил статус пользователя", "User", target.login
        )
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/delete")
async def delete_user(
    request: Request,
    user_id: int = Form(...),
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    target = db.query(User).filter(User.id == user_id).first()
    if target and target.id != current_user.id and target.login != "admin":
        name = target.login
        db.delete(target)
        db.commit()
        write_audit_log(db, request, current_user, "Удалил пользователя", "User", name)

    return RedirectResponse("/admin/users", status_code=303)


@app.get("/admin/ips")
async def admin_ips(
    request: Request,
    user_id: Optional[int] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    users = db.query(User).order_by(User.login).all()
    selected_user = None
    if user_id:
        selected_user = db.query(User).filter(User.id == user_id).first()
    elif users:
        selected_user = users[0]

    return templates.TemplateResponse(
        request=request,
        name="admin/ips.html",
        context={
            "user": current_user,
            "users": users,
            "selected_user": selected_user,
            "active_page": "ips",
        },
    )


@app.post("/admin/ips/add")
async def add_ip(
    request: Request,
    user_id: int = Form(...),
    ip_value: str = Form(...),
    description: str = Form(""),
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    ip_value = ip_value.strip()
    target = db.query(User).filter(User.id == user_id).first()
    exists = (
        db.query(AllowedIP)
        .filter(AllowedIP.user_id == user_id, AllowedIP.ip_address == ip_value)
        .first()
    )
    if target and valid_ip(ip_value) and not exists:
        db.add(
            AllowedIP(
                user_id=user_id,
                ip_address=ip_value,
                description=description.strip(),
            )
        )
        db.commit()
        write_audit_log(db, request, current_user, "Добавил IP", "User", target.login)

    return RedirectResponse(f"/admin/ips?user_id={user_id}", status_code=303)


@app.post("/admin/ips/delete")
async def delete_ip(
    request: Request,
    ip_id: int = Form(...),
    user_id: int = Form(...),
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    item = db.query(AllowedIP).filter(AllowedIP.id == ip_id).first()
    if item:
        db.delete(item)
        db.commit()
        write_audit_log(db, request, current_user, "Удалил IP", "AllowedIP", str(ip_id))

    return RedirectResponse(f"/admin/ips?user_id={user_id}", status_code=303)


@app.get("/admin/login-logs")
async def admin_login_logs(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    logs = db.query(LoginLog).order_by(LoginLog.login_time.desc()).limit(300).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/login_logs.html",
        context={"user": current_user, "logs": logs, "active_page": "login_logs"},
    )


@app.get("/admin/audit-logs")
async def admin_audit_logs(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(300).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/audit_logs.html",
        context={"user": current_user, "logs": logs, "active_page": "audit_logs"},
    )


@app.get("/admin/sessions")
async def admin_sessions(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    sessions = (
        db.query(SessionLog)
        .filter(SessionLog.is_active.is_(True))
        .order_by(SessionLog.last_activity.desc())
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/sessions.html",
        context={"user": current_user, "sessions": sessions, "active_page": "sessions"},
    )


@app.post("/admin/sessions/kill")
async def kill_session(
    request: Request,
    session_id: int = Form(...),
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not admin_required(current_user):
        return RedirectResponse("/login", status_code=303)

    target = db.query(SessionLog).filter(SessionLog.id == session_id).first()
    if target:
        target.is_active = False
        db.commit()
        write_audit_log(
            db,
            request,
            current_user,
            "Завершил сессию",
            "Session",
            str(session_id),
        )
    return RedirectResponse("/admin/sessions", status_code=303)


# @app.get("/admin/settings")
# async def admin_settings(
#     request: Request,
#     current_user: Optional[User] = Depends(get_current_user),
#     db: Session = Depends(get_db),
# ):
#     if not admin_required(current_user):
#         return RedirectResponse("/login", status_code=303)

#     settings = {item.key: item.value for item in db.query(Setting).all()}
#     return templates.TemplateResponse(
#         request=request,
#         name="admin/settings.html",
#         context={"user": current_user, "settings": settings, "active_page": "settings"},
#     )
