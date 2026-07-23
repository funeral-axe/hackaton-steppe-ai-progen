import token

#from django.contrib.gis import db
#from requests import request, request, session

from fastapi import FastAPI, Depends, Request, Form, Response, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import SessionLocal, User, verify_password, get_password_hash
from audio_processor import process_audio_files
from datetime import datetime
from database import AllowedIP
from database import AuditLog
from datetime import datetime
from uuid import uuid4
from database import SessionLog

app = FastAPI()
templates = Jinja2Templates(directory="templates")


# Зависимость для получения сессии БД
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Функция для записи действий
def write_audit_log(
    db: Session,
    user: User,
    action: str,
    object_type: str,
    object_name: str,
    ip_address: str,
):
    log = AuditLog(
        user_id=user.id,
        action=action,
        object_type=object_type,
        object_name=object_name,
        ip_address=ip_address,
        created_at=datetime.utcnow(),
    )

    db.add(log)
    db.commit()


# Зависимость для получения текущего пользователя из куки
def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("session_token")

    if not token:
        return None

    session = (
        db.query(SessionLog)
        .filter(SessionLog.session_token == token, SessionLog.is_active == True)
        .first()
    )

    if not session:
        return None

    session.last_activity = datetime.utcnow()
    db.commit()

    return session.user


@app.get("/login")
async def login_page(request: Request):
    # Исправленный синтаксис TemplateResponse
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/login")
async def login_post(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.login == login).first()
    if not user or not verify_password(password, user.password):
        if not user.is_active:
            return RedirectResponse("/login?error=blocked", status_code=303)

        return RedirectResponse(url="/login?error=1", status_code=303)
    client_ip = request.client.host
    if not is_ip_allowed(user, client_ip):
        return RedirectResponse(url="/login?error=ip_denied", status_code=303)
    user.last_login = datetime.utcnow()
    db.commit()

    session_token = str(uuid4())

    session = SessionLog(
        user_id=user.id,
        session_token=session_token,
        ip_address=request.client.host,
        browser=request.headers.get("user-agent"),
        is_active=True,
    )
    db.add(session)
    db.commit()

    # Успешная авторизация, перенаправление в зависимости от роли
    redirect_url = "/admin" if user.role == "admin" else "/"
    res = RedirectResponse(url=redirect_url, status_code=303)

    # Устанавливаем куку с логином (в реальном проекте здесь должен быть JWT токен)
    res.set_cookie(
        key="session_token", value=session_token, httponly=True, samesite="lax"
    )
    return res


@app.get("/logout")
async def logout():
    res = RedirectResponse(url="/login", status_code=303)
    token = request.cookies.get("session_token")

    session = db.query(SessionLog).filter(SessionLog.session_token == token).first()

    if session:
        session.is_active = False
        db.commit()
    res.delete_cookie("session_token")
    return res


@app.get("/")
async def index_page(request: Request, user: User = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if user.role == "admin":
        return RedirectResponse(url="/admin", status_code=303)

    # Страница обычного пользователя (поиск)
    return templates.TemplateResponse(
        request=request, name="index.html", context={"user": user}
    )


@app.post("/search")
async def start_search(
    background_tasks: BackgroundTasks,
    search_mode: str = Form(...),
    query: str = Form(...),
    source_folder: str = Form(...),
    user: User = Depends(get_current_user),
):
    if not user or user.role != "user":
        return RedirectResponse(url="/login", status_code=303)

    # Запуск фоновой задачи для обработки 10 млн файлов
    background_tasks.add_task(process_audio_files, search_mode, query, source_folder)

    # Возвращаем на главную с сообщением
    return RedirectResponse(url="/?msg=Поиск+запущен+в+фоне", status_code=303)


@app.get("/admin")
async def admin_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user or user.role != "admin":
        return RedirectResponse(url="/login", status_code=303)

    users = db.query(User).all()
    # Страница администратора
    return templates.TemplateResponse(
        request=request, name="admin.html", context={"user": user, "users": users}
    )


@app.post("/admin/add_user")
async def add_user(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user or user.role != "admin":
        return RedirectResponse(url="/login", status_code=303)

    # Проверка, существует ли уже такой логин
    existing_user = db.query(User).filter(User.login == login).first()
    if not existing_user:
        new_user = User(login=login, password=get_password_hash(password), role=role)
        db.add(new_user)
        db.commit()
    write_audit_log(
        db=db,
        user=user,
        action="Создал пользователя",
        object_type="User",
        object_name=new_user.login,
        ip_address=request.client.host,
    )
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/edit_user")
async def edit_user(
    user_id: int = Form(...),
    login: str = Form(...),
    role: str = Form(...),
    is_active: bool = Form(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user or current_user.role != "admin":
        return RedirectResponse("/login", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()

    if user:
        user.login = login
        user.role = role
        user.is_active = is_active
        db.commit()

    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/delete_user")
async def delete_user(
    request: Request,
    user_id: int = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user or current_user.role != "admin":
        return RedirectResponse("/login", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()

    if user and user.login != "admin":
        db.delete(user)
        db.commit()
    write_audit_log(
        db=db,
        user=current_user,
        action="Удалил пользователя",
        object_type="User",
        object_name=user.login,
        ip_address=request.client.host,
    )

    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/toggle_user")
async def toggle_user(
    request: Request,
    user_id: int = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user or current_user.role != "admin":
        return RedirectResponse("/login", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()

    if user and user.login != "admin":
        user.is_active = not user.is_active
        db.commit()
    write_audit_log(
        db=db,
        user=current_user,
        action="Изменил статус пользователя",
        object_type="User",
        object_name=user.login,
        ip_address=request.client.host,
    )

    return RedirectResponse("/admin", status_code=303)


# zapret po ip
@app.post("/admin/add_ip")
async def add_ip(
    user_id: int = Form(...),
    ip_address: str = Form(...),
    description: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user or current_user.role != "admin":
        return RedirectResponse("/login", status_code=303)

    exists = (
        db.query(AllowedIP)
        .filter(AllowedIP.user_id == user_id, AllowedIP.ip_address == ip_address)
        .first()
    )

    if not exists:
        db.add(
            AllowedIP(user_id=user_id, ip_address=ip_address, description=description)
        )
        db.commit()

    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/delete_ip")
async def delete_ip(
    ip_id: int = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user or current_user.role != "admin":
        return RedirectResponse("/login", status_code=303)

    ip = db.query(AllowedIP).filter(AllowedIP.id == ip_id).first()

    if ip:
        db.delete(ip)
        db.commit()

    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/audit_logs")
async def audit_logs(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user or current_user.role != "admin":
        return RedirectResponse("/login", status_code=303)

    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).all()

    return templates.TemplateResponse(
        request=request,
        name="audit_logs.html",
        context={"user": current_user, "logs": logs},
    )


# Проверка IP при входе
def is_ip_allowed(user: User, ip: str) -> bool:
    if not user.allowed_ips:
        return True

    for allowed in user.allowed_ips:
        if allowed.ip_address == ip:
            return True

    return False


@app.get("/admin/sessions")
async def sessions_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user or current_user.role != "admin":
        return RedirectResponse("/login", status_code=303)

    sessions = db.query(SessionLog).filter(SessionLog.is_active == True).all()

    return templates.TemplateResponse(
        "sessions.html",
        {"request": request, "sessions": sessions, "user": current_user},
    )


@app.post("/admin/kill_session")
async def kill_session(
    session_id: int = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user or current_user.role != "admin":
        return RedirectResponse("/login", status_code=303)

    session = db.query(SessionLog).get(session_id)

    if session:
        session.is_active = False
        db.commit()

    return RedirectResponse("/admin/sessions", status_code=303)
