"""Переносимая схема хранения; изменения таблиц применяются миграциями Alembic.

Аккаунт объединяет устройства, диалог объединяет сообщения и заявку.
Схема общая для SQLite и PostgreSQL; особенности подключения изолированы ниже.
"""

import os
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import (JSON, Boolean, CheckConstraint, Column, ForeignKey, Integer,
                        MetaData, String, Table, Text, UniqueConstraint, create_engine, event)


def uid():
    """Строковый UUID одинаково хранится в обеих поддерживаемых СУБД."""
    return str(uuid4())


def now():
    """Единый формат ISO 8601 в UTC; отображение местного времени делает интерфейс."""
    return datetime.now(timezone.utc).isoformat()


# Стабильные имена ограничений нужны Alembic, чтобы находить их при изменении схемы.
metadata = MetaData(naming_convention={
    "ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
})
# Учётная запись не совпадает с сессией входа: один пользователь имеет несколько устройств.
users = Table("users", metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(254), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("is_staff", Boolean, nullable=False, default=False),
    Column("created_at", String(40), nullable=False))
# Секрет cookie не сохраняется открытым текстом; для поиска используется его хеш.
# CSRF-токен связан с конкретной сессией, а не со всеми входами пользователя.
sessions = Table("auth_sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False, index=True),
    Column("csrf_token", String(64), nullable=False),
    Column("expires_at", String(40), nullable=False))
# Закрытый диалог остаётся в архиве. version обнаруживает конкурентные изменения.
dialogs = Table("dialogs", metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False, index=True),
    Column("status", String(20), nullable=False, default="active"),
    Column("version", Integer, nullable=False, default=0),
    Column("created_at", String(40), nullable=False),
    CheckConstraint("status IN ('active', 'closed')", name="status"))
# Один request_id связывает пользовательское сообщение и ответ ассистента.
# Уникальность этой тройки защищает от дублирования при повторной HTTP-отправке.
# sources — основания ответа; result_meta — действие и черновик резюме для повторного ответа API.
messages = Table("messages", metadata,
    Column("id", String(36), primary_key=True),
    Column("dialog_id", String(36), ForeignKey("dialogs.id"), nullable=False, index=True),
    Column("request_id", String(36), nullable=False),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("sources", JSON, nullable=False),
    Column("result_meta", JSON, nullable=False),
    Column("elapsed_ms", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("dialog_id", "request_id", "role"),
    CheckConstraint("role IN ('user', 'assistant')", name="role"))
# Одна карточка/заявка на диалог. Переход draft → created требует согласия
# с конкретной version. consent_at фиксирует время успешного подтверждения.
applications = Table("applications", metadata,
    Column("id", String(36), primary_key=True),
    Column("dialog_id", String(36), ForeignKey("dialogs.id"), nullable=False, unique=True),
    Column("full_name", String(200), nullable=False),
    Column("phone", String(30), nullable=False),
    Column("company", String(300), nullable=False),
    Column("summary", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("version", Integer, nullable=False, default=1),
    Column("consent_at", String(40)),
    Column("created_at", String(40), nullable=False),
    CheckConstraint("status IN ('draft', 'created')", name="status"))


def make_engine(url=None):
    """Выбираем БД через DATABASE_URL; запросы приложения остаются общими."""
    url = url or os.getenv("DATABASE_URL", "sqlite:///./energytool.db")
    options = {"check_same_thread": False, "timeout": 10} if url.startswith("sqlite") else {}
    # FastAPI выполняет синхронные обработчики в рабочих потоках. Для SQLite
    # разрешаем использование соединения в них; ожидание блокировки ограничено 10 с.
    # hide_parameters не даёт SQLAlchemy включать значения SQL-параметров в ошибки.
    engine = create_engine(url, connect_args=options, hide_parameters=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            # SQLite требует явного включения внешних ключей на каждом соединении.
            connection.execute("PRAGMA foreign_keys=ON")
            # WAL позволяет чтению сосуществовать с записью; одновременно пишущий
            # процесс по-прежнему один. Это не превращает SQLite в серверную СУБД.
            connection.execute("PRAGMA journal_mode=WAL")
    return engine
