from fastapi import FastAPI, Depends, Request, Form, Response, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import SessionLocal, User, verify_password, get_password_hash
from audio_processor import process_audio_files

app = FastAPI()
templates = Jinja2Templates(directory="templates")


# Зависимость для получения сессии БД
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Зависимость для получения текущего пользователя из куки
def get_current_user(request: Request, db: Session = Depends(get_db)):
    login = request.cookies.get("session_user")
    if not login:
        return None
    return db.query(User).filter(User.login == login).first()


@app.get("/login")
async def login_page(request: Request):
    # Исправленный синтаксис TemplateResponse
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/login")
async def login_post(
        response: Response,
        login: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.login == login).first()
    if not user or not verify_password(password, user.password):
        # При ошибке возвращаем на страницу входа
        return RedirectResponse(url="/login?error=1", status_code=303)

    # Успешная авторизация, перенаправление в зависимости от роли
    redirect_url = "/admin" if user.role == "admin" else "/"
    res = RedirectResponse(url=redirect_url, status_code=303)

    # Устанавливаем куку с логином (в реальном проекте здесь должен быть JWT токен)
    res.set_cookie(key="session_user", value=user.login, httponly=True)
    return res


@app.get("/logout")
async def logout():
    res = RedirectResponse(url="/login", status_code=303)
    res.delete_cookie("session_user")
    return res


@app.get("/")
async def index_page(request: Request, user: User = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if user.role == "admin":
        return RedirectResponse(url="/admin", status_code=303)

    # Страница обычного пользователя (поиск)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": user}
    )


@app.post("/search")
async def start_search(
        background_tasks: BackgroundTasks,
        search_mode: str = Form(...),
        query: str = Form(...),
        source_folder: str = Form(...),
        user: User = Depends(get_current_user)
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
        db: Session = Depends(get_db)
):
    if not user or user.role != "admin":
        return RedirectResponse(url="/login", status_code=303)

    users = db.query(User).all()
    # Страница администратора
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"user": user, "users": users}
    )


@app.post("/admin/add_user")
async def add_user(
        login: str = Form(...),
        password: str = Form(...),
        role: str = Form(...),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    if not user or user.role != "admin":
        return RedirectResponse(url="/login", status_code=303)

    # Проверка, существует ли уже такой логин
    existing_user = db.query(User).filter(User.login == login).first()
    if not existing_user:
        new_user = User(
            login=login,
            password=get_password_hash(password),
            role=role
        )
        db.add(new_user)
        db.commit()

    return RedirectResponse(url="/admin", status_code=303)