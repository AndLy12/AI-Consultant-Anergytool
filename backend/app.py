"""HTTP API прототипа: аккаунты, диалоги, история и подтверждённые заявки.

Поиск и генерация делегируются общему ядру роли 2. Этот модуль отвечает
за права доступа, проверку входных данных и сохранение результатов в БД.
"""

import os
import re
import secrets
import time
from collections import defaultdict, deque
from threading import Lock
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update, delete
from sqlalchemy.exc import IntegrityError

from backend import db
from backend.core import load_core
from backend.security import DUMMY_HASH, digest, passwords
from core_contract import CoreRequest, CoreResponse, Turn


class Input(BaseModel):
    """Общие правила запросов: неизвестные поля запрещены, пробелы обрезаются."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Credentials(Input):
    # Для пароля отключаем обрезку: пробел может быть частью выбранного пароля.
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        """Нормализуем логин; проверка формы адреса не подтверждает владение почтой."""
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid email")
        return value.lower()


class MessageInput(Input):
    # Клиент создаёт UUID один раз на отправку; повтор запроса использует тот же ID.
    request_id: UUID
    content: str = Field(min_length=1, max_length=10000)


class ApplicationInput(Input):
    """Данные карточки, которые пользователь проверяет перед созданием заявки."""
    full_name: str = Field(min_length=2, max_length=200)
    phone: str = Field(min_length=7, max_length=30)
    company: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=10000)

    @field_validator("phone")
    @classmethod
    def phone_valid(cls, value):
        # Допускаем привычное оформление номера, проверяем допустимые знаки и число цифр.
        # Это проверка формата, а не подтверждение существования номера.
        if not re.fullmatch(r"\+?[\d\s()\-]+", value) or not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("Invalid phone")
        return value


class Confirmation(Input):
    # Версия связывает согласие именно с той карточкой, которую видел пользователь.
    version: int = Field(ge=1)
    consent: bool


def create_app(database_url=None, core=None):
    """Создаём приложение; тесты могут подставить отдельную БД и тестовое ядро."""
    engine = db.make_engine(database_url)

    @asynccontextmanager
    async def lifespan(app):
        """Один раз загружаем ядро и восстанавливаем статус прерванных запросов."""
        app.state.core = core if core is not None else load_core()
        # Рассчитано на один процесс: после его перезапуска прежняя генерация уже
        # не выполняется. Отмечаем её ошибкой, сохраняя исходное сообщение в истории.
        # При нескольких репликах потребуется другой механизм восстановления.
        with engine.begin() as connection:
            connection.execute(update(db.messages).where(db.messages.c.status == "processing").values(status="failed"))
        try:
            yield
        finally:
            # Закрываем и async-адаптер, и синхронное ядро Данька через их
            # необязательный close(). Соединения не должны переживать приложение.
            try:
                close = getattr(app.state.core, "close", None)
                if callable(close):
                    close()
            finally:
                engine.dispose()

    app = FastAPI(title="Energytool backend", version="1.0.0", lifespan=lifespan)
    app.state.engine = engine
    cookie_secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    allowed_origin = os.getenv("APP_ORIGIN", "http://localhost:8000").rstrip("/")
    attempts, attempts_lock = defaultdict(deque), Lock()

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        """Общие ограничения входа, проверка источника запроса и защитные заголовки."""
        if request.url.path in {"/api/auth/login", "/api/auth/register"} and request.method == "POST":
            # Ограничитель действует в памяти одного процесса. X-Forwarded-For
            # не используем: без настройки доверенных прокси клиент может его подменить.
            key = request.client.host if request.client else "unknown"
            with attempts_lock:
                current = time.monotonic()
                for host in list(attempts):
                    while attempts[host] and attempts[host][0] < current - 60:
                        attempts[host].popleft()
                    if not attempts[host]:
                        del attempts[host]
                bucket = attempts[key]
                if len(bucket) >= 15:
                    from fastapi.responses import JSONResponse
                    return JSONResponse({"detail": "Too many attempts; retry in a minute"}, status_code=429)
                bucket.append(current)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            # Браузерные изменения принимаем только с нашего сайта. Отсутствующий
            # Origin допустим для API-клиентов; защищённые операции дополнительно требуют CSRF.
            origin = request.headers.get("origin")
            if origin and origin != allowed_origin:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Origin rejected"}, status_code=403)
        response = await call_next(request)
        # Не кэшируем персональные ответы и не разрешаем сторонние скрипты/встраивание.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        return response

    def authenticated(request: Request):
        """Находим действующую сессию и проверяем CSRF для изменения данных."""
        token = request.cookies.get("energytool_session", "")
        with engine.connect() as connection:
            row = connection.execute(select(db.users, db.sessions.c.csrf_token).join(
                db.sessions, db.sessions.c.user_id == db.users.c.id).where(
                    db.sessions.c.token_hash == digest(token), db.sessions.c.expires_at > db.now())).mappings().first()
        if row is None:
            raise HTTPException(401, "Authentication required")
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), row["csrf_token"]):
            raise HTTPException(403, "CSRF token required")
        return dict(row)

    def staff(user=Depends(authenticated)):
        """Общий барьер для операций сотрудника, доступных поверх обычного входа."""
        if not user["is_staff"]:
            raise HTTPException(403, "Staff access required")
        return user

    def owned(connection, dialog_id, user):
        """Ищем диалог одновременно по ID и владельцу, а не доверяем ID из URL."""
        row = connection.execute(select(db.dialogs).where(
            db.dialogs.c.id == dialog_id, db.dialogs.c.user_id == user["id"])).mappings().first()
        if row is None:
            # Для чужого и несуществующего диалога ответ одинаков: не раскрываем его наличие.
            raise HTTPException(404, "Dialog not found")
        return row

    def public_user(user):
        """Явно выбираем публичные поля, исключая хеш пароля и данные сессии."""
        return {"id": user["id"], "email": user["email"], "is_staff": user["is_staff"]}

    def start_session(connection, user_id, response):
        """Создаём отдельную сессию устройства; исходный токен передаём только cookie."""
        token, csrf = secrets.token_urlsafe(32), secrets.token_hex(32)
        connection.execute(insert(db.sessions).values(token_hash=digest(token), user_id=user_id,
            csrf_token=csrf, expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat()))
        response.set_cookie("energytool_session", token, httponly=True, secure=cookie_secure,
                            samesite="lax", max_age=604800, path="/")
        return csrf

    @app.get("/health")
    def health():
        # Проверяем доступность схемы БД; этот маршрут не проверяет реальный вызов Qwen.
        with engine.connect() as connection:
            connection.execute(select(db.users.c.id).limit(1))
        # real означает загрузку фабрики, но не обязательно настоящую модель:
        # у Данька CORE_DEMO=true включает синтетическую базу и тестовый клиент.
        from backend.core import MissingCore
        demo = bool(getattr(getattr(app.state.core, "settings", None), "demo", False))
        return {"status": "ok", "core_mode": os.getenv("CORE_MODE", "real"),
                "core_demo": demo, "core_ready": not isinstance(app.state.core, MissingCore)}

    @app.post("/api/auth/register", status_code=201)
    def register(body: Credentials, response: Response):
        """Сохраняем хеш пароля и сразу открываем обычную пользовательскую сессию."""
        user_id = db.uid()
        hashed = passwords.hash(body.password)
        try:
            with engine.begin() as connection:
                connection.execute(insert(db.users).values(id=user_id, email=body.email,
                    password_hash=hashed, is_staff=False, created_at=db.now()))
                csrf = start_session(connection, user_id, response)
        except IntegrityError:
            # Уникальность email защищает и от одновременной регистрации одного адреса.
            raise HTTPException(409, "Registration unavailable for this email")
        return {"user": {"id": user_id, "email": body.email, "is_staff": False}, "csrf_token": csrf}

    @app.post("/api/auth/login")
    def login(body: Credentials, response: Response):
        """Проверяем пароль и создаём новую сессию, сохраняя входы других устройств."""
        with engine.begin() as connection:
            user = connection.execute(select(db.users).where(db.users.c.email == body.email)).mappings().first()
            verified = passwords.verify(body.password, user["password_hash"] if user else DUMMY_HASH)
            # Для неизвестного email тоже вычисляется дорогая проверка хеша;
            # текст ошибки не сообщает, существует ли такой аккаунт.
            if not user or not verified:
                raise HTTPException(401, "Invalid email or password")
            csrf = start_session(connection, user["id"], response)
        return {"user": public_user(user), "csrf_token": csrf}

    @app.get("/api/auth/me")
    def me(user=Depends(authenticated)):
        # После обновления страницы frontend получает аккаунт и CSRF текущей сессии.
        return {"user": public_user(user), "csrf_token": user["csrf_token"]}

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, response: Response, user=Depends(authenticated)):
        # Удаляем только текущий токен: на остальных устройствах пользователь остаётся в системе.
        with engine.begin() as connection:
            connection.execute(delete(db.sessions).where(
                db.sessions.c.token_hash == digest(request.cookies.get("energytool_session", ""))))
        response.delete_cookie("energytool_session", path="/")

    @app.post("/api/dialogs", status_code=201)
    def new_dialog(user=Depends(authenticated)):
        """Архивируем активный контекст аккаунта и создаём пустой диалог."""
        dialog = {"id": db.uid(), "user_id": user["id"], "status": "active", "version": 0, "created_at": db.now()}
        with engine.begin() as connection:
            # Обновление строки пользователя сериализует параллельное создание диалогов.
            # Закрытие старого и создание нового выполняются в одной транзакции.
            connection.execute(update(db.users).where(db.users.c.id == user["id"]).values(email=user["email"]))
            connection.execute(update(db.dialogs).where(db.dialogs.c.user_id == user["id"],
                db.dialogs.c.status == "active").values(status="closed", version=db.dialogs.c.version + 1))
            connection.execute(insert(db.dialogs).values(**dialog))
        return dialog

    @app.get("/api/dialogs")
    def list_dialogs(user=Depends(authenticated)):
        """Возвращаем историю диалогов только текущего аккаунта."""
        with engine.connect() as connection:
            return list(connection.execute(select(db.dialogs).where(db.dialogs.c.user_id == user["id"])
                .order_by(db.dialogs.c.created_at)).mappings())

    @app.get("/api/dialogs/{dialog_id}")
    def get_dialog(dialog_id: str, user=Depends(authenticated)):
        """Чтение архива не возобновляет диалог и не меняет контекст ядра."""
        with engine.connect() as connection:
            dialog = dict(owned(connection, dialog_id, user))
            dialog["messages"] = list(connection.execute(select(db.messages).where(db.messages.c.dialog_id == dialog_id)
                .order_by(db.messages.c.created_at, db.messages.c.role.desc())).mappings())
        return dialog

    @app.post("/api/dialogs/{dialog_id}/close", status_code=204)
    def close_dialog(dialog_id: str, user=Depends(authenticated)):
        # Сохраняем сообщения, но запрещаем дальнейшую генерацию в закрытом диалоге.
        with engine.begin() as connection:
            owned(connection, dialog_id, user)
            connection.execute(update(db.dialogs).where(db.dialogs.c.id == dialog_id)
                .values(status="closed", version=db.dialogs.c.version + 1))

    @app.post("/api/dialogs/{dialog_id}/messages")
    def send_message(dialog_id: str, body: MessageInput, user=Depends(authenticated)):
        """Сохраняем запрос, вызываем ядро без транзакции и атомарно записываем ответ."""
        request_id = str(body.request_id)
        with engine.begin() as connection:
            dialog = owned(connection, dialog_id, user)
            existing = connection.execute(select(db.messages).where(db.messages.c.dialog_id == dialog_id,
                db.messages.c.request_id == request_id)).mappings().all()
            if existing:
                # Повтор после потери HTTP-ответа не вызывает модель второй раз.
                # Тот же request_id нельзя переиспользовать для другого текста.
                original = next(x for x in existing if x["role"] == "user")
                if original["content"] != body.content:
                    raise HTTPException(409, "Request ID already used for another message")
                answer = next((x for x in existing if x["role"] == "assistant"), None)
                if answer:
                    return {**answer, **answer["result_meta"]}
                raise HTTPException(409, "Request is processing or failed; use a new request ID after failure")
            if dialog["status"] != "active":
                raise HTTPException(409, "Start a new dialog to continue")
            pending = connection.execute(select(db.messages.c.id).where(db.messages.c.dialog_id == dialog_id,
                db.messages.c.status == "processing")).first()
            if pending:
                raise HTTPException(409, "Another message is processing")
            # Проверяем версию при записи: параллельный запрос мог уже изменить диалог.
            # Успех только у запроса, который застал актуальную версию строки.
            claimed = connection.execute(update(db.dialogs).where(db.dialogs.c.id == dialog_id,
                db.dialogs.c.version == dialog["version"], db.dialogs.c.status == "active")
                .values(version=db.dialogs.c.version + 1))
            if claimed.rowcount != 1:
                raise HTTPException(409, "Dialog changed; retry")
            history = connection.execute(select(db.messages).where(db.messages.c.dialog_id == dialog_id,
                db.messages.c.status == "completed").order_by(db.messages.c.created_at)).mappings().all()
            message_id = db.uid()
            # Фиксируем пользовательский текст до обращения к модели: при её сбое
            # история не потеряется. Незавершённые/ошибочные обмены ядру не передаём.
            connection.execute(insert(db.messages).values(id=message_id, dialog_id=dialog_id, request_id=request_id,
                role="user", content=body.content, status="processing", sources=[], result_meta={}, elapsed_ms=0, created_at=db.now()))
        # Транзакция уже завершена: ожидание Qwen не удерживает блокировку записи БД.
        # Ограниченные сетевые таймауты и повторы обеспечивает реализация ядра.
        started = time.monotonic()
        try:
            result = CoreResponse.model_validate(app.state.core.respond(CoreRequest(message=body.content,
                history=[Turn(role=x["role"], content=x["content"]) for x in history])))
        except Exception:
            # Даже невалидный результат ядра считаем техническим сбоем. Исключение
            # не отдаём клиенту: оно может содержать внутренние данные провайдера.
            with engine.begin() as connection:
                connection.execute(update(db.messages).where(db.messages.c.id == message_id).values(status="failed"))
            raise HTTPException(503, "Consultant temporarily unavailable; your message was saved")
        elapsed = round((time.monotonic() - started) * 1000)
        answer = dict(id=db.uid(), dialog_id=dialog_id, request_id=request_id, role="assistant",
                      content=result.answer, status="completed", sources=[s.model_dump() for s in result.sources],
                      result_meta=result.model_dump(exclude={"answer", "sources"}),
                      elapsed_ms=elapsed, created_at=db.now())
        with engine.begin() as connection:
            connection.execute(update(db.messages).where(db.messages.c.id == message_id).values(status="completed"))
            # Статус запроса и ответ фиксируем вместе: частично завершённый обмен не сохраняется.
            connection.execute(insert(db.messages).values(**answer))
        return {**answer, **answer["result_meta"]}

    @app.put("/api/dialogs/{dialog_id}/application")
    def draft_application(dialog_id: str, body: ApplicationInput, user=Depends(authenticated)):
        """Сохраняем редактируемую карточку; само сохранение ещё не создаёт заявку."""
        with engine.begin() as connection:
            owned(connection, dialog_id, user)
            # Запись строки диалога упорядочивает конкурентные сохранения и подтверждения.
            # Это работает и на PostgreSQL, где блокируется изменяемая строка.
            connection.execute(update(db.dialogs).where(db.dialogs.c.id == dialog_id)
                .values(version=db.dialogs.c.version + 1))
            existing = connection.execute(select(db.applications).where(db.applications.c.dialog_id == dialog_id)).mappings().first()
            if existing and existing["status"] == "created":
                raise HTTPException(409, "Application already confirmed")
            values = body.model_dump()
            if existing:
                # Каждое редактирование меняет версию: старое согласие станет недействительным.
                values["version"] = existing["version"] + 1
                connection.execute(update(db.applications).where(db.applications.c.id == existing["id"]).values(**values))
                application_id = existing["id"]
            else:
                application_id = db.uid()
                connection.execute(insert(db.applications).values(id=application_id, dialog_id=dialog_id,
                    status="draft", version=1, created_at=db.now(), **values))
            return dict(connection.execute(select(db.applications).where(db.applications.c.id == application_id)).mappings().one())

    @app.get("/api/dialogs/{dialog_id}/application")
    def get_application(dialog_id: str, user=Depends(authenticated)):
        """Карточка и контакты доступны владельцу соответствующего диалога."""
        with engine.connect() as connection:
            owned(connection, dialog_id, user)
            result = connection.execute(select(db.applications).where(db.applications.c.dialog_id == dialog_id)).mappings().first()
            if not result:
                raise HTTPException(404, "Application not found")
            return dict(result)

    @app.post("/api/dialogs/{dialog_id}/application/confirm")
    def confirm(dialog_id: str, body: Confirmation, user=Depends(authenticated)):
        """Создаём заявку по явному согласию с актуальной версией карточки."""
        if not body.consent:
            raise HTTPException(422, "Explicit confirmation required")
        with engine.begin() as connection:
            owned(connection, dialog_id, user)
            connection.execute(update(db.dialogs).where(db.dialogs.c.id == dialog_id).values(version=db.dialogs.c.version + 1))
            application = connection.execute(select(db.applications).where(db.applications.c.dialog_id == dialog_id)).mappings().first()
            if not application:
                raise HTTPException(404, "Prepare the confirmation card first")
            if application["version"] != body.version:
                raise HTTPException(409, "Card changed; review the latest version")
            if application["status"] == "draft":
                # Повтор подтверждения созданной заявки пропускает запись и возвращает
                # тот же ID. Внешнюю отправку в CRM или менеджеру здесь не выполняем.
                connection.execute(update(db.applications).where(db.applications.c.id == application["id"])
                    .values(status="created", consent_at=db.now()))
            return dict(connection.execute(select(db.applications).where(db.applications.c.id == application["id"])).mappings().one())

    @app.get("/api/staff/dialogs")
    def staff_dialogs(user=Depends(staff)):
        """Сотрудник может проверить все сессии, включая диалоги без заявки."""
        with engine.connect() as connection:
            return list(connection.execute(select(db.dialogs).order_by(db.dialogs.c.created_at)).mappings())

    @app.get("/api/staff/dialogs/{dialog_id}")
    def staff_dialog(dialog_id: str, user=Depends(staff)):
        """Отдельный административный маршрут: права проверены зависимостью staff."""
        with engine.connect() as connection:
            dialog = connection.execute(select(db.dialogs).where(db.dialogs.c.id == dialog_id)).mappings().first()
            if not dialog:
                raise HTTPException(404, "Dialog not found")
            return {**dialog, "messages": list(connection.execute(select(db.messages).where(
                db.messages.c.dialog_id == dialog_id).order_by(db.messages.c.created_at)).mappings())}

    @app.get("/api/staff/applications")
    def staff_applications(user=Depends(staff)):
        """Возвращаем заявки для проверки сотрудником, не публичному пользователю."""
        with engine.connect() as connection:
            return list(connection.execute(select(db.applications)).mappings())

    @app.get("/")
    def index():
        # Проверочный frontend раздаётся с того же origin, что и API: отдельный CORS не нужен.
        return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))

    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
    return app
