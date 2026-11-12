"""Подключение Alembic к той же БД и метаданным, что использует приложение."""

from alembic import context
from backend.db import make_engine, metadata

if context.is_offline_mode():
    raise RuntimeError("Use online migrations with DATABASE_URL")
engine = make_engine()
# Для SQLite изменения таблиц могут требовать их пересоздания; batch-режим
# обеспечивает это, сохраняя обычный путь ALTER TABLE на PostgreSQL.
try:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata,
                          render_as_batch=connection.dialect.name == "sqlite",
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
finally:
    # Закрытие connection возвращает его в пул; dispose освобождает и сам пул.
    engine.dispose()
