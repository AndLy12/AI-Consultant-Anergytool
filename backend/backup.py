"""Резервирование SQLite онлайн и восстановление только в новый файл.

Используем SQLite Backup API, чтобы согласованно прочитать и основной файл, и WAL.
PostgreSQL обслуживается штатными pg_dump/pg_restore; команды приложения и схема
при этом остаются общими. Копии содержат аккаунты и историю, поэтому создаются 0600.
"""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from backend.db import metadata

ROOT = Path(__file__).resolve().parents[1]


def database_path():
    """Не создаём БД из опечатки в URL и не трактуем PostgreSQL как файл SQLite."""
    url = make_url(os.getenv("DATABASE_URL", "sqlite:///./energytool.db"))
    if url.get_backend_name() != "sqlite":
        raise ValueError("Для PostgreSQL используйте pg_dump/pg_restore")
    if not url.database or url.database == ":memory:" or url.query:
        raise ValueError("Нужна файловая SQLite без URI-параметров")
    return Path(url.database).expanduser().resolve()


def readonly(path):
    """mode=ro запрещает создание отсутствующего источника и запись в него."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Файл источника не найден")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)


def verify(source):
    """Проверяем целостность, связи и текущую схему, не выводя содержимое записей."""
    expected = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    with closing(readonly(source)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Проверка целостности SQLite не пройдена")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Нарушены внешние ключи")
        revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        if revisions != [(expected,)]:
            raise ValueError("Версия схемы не совпадает с текущей; используйте соответствующий релиз")
        counts = {}
        # Имена берём из схемы приложения, а не из пользовательского SQL.
        for table in metadata.sorted_tables:
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table.name}")')}
            if not set(table.c.keys()).issubset(columns):
                raise ValueError("В резервной копии отсутствуют обязательные поля")
            counts[table.name] = connection.execute(f'SELECT count(*) FROM "{table.name}"').fetchone()[0]
    return {"revision": expected, "counts": counts}


def snapshot(source, destination, timeout=30):
    """Публикуем проверенную копию; существующий файл никогда не перезаписываем.

    Ограничение времени охватывает ожидание блокировок Backup API. При ошибке
    удаляем только собственный новый файл. Оператор не должен использовать
    destination до успешного завершения команды.
    """
    destination = Path(destination).expanduser().absolute()
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        deadline = time.monotonic() + timeout

        def progress(_status, _remaining, _total):
            if time.monotonic() >= deadline:
                raise TimeoutError("Истекло время создания копии")

        with closing(readonly(source)) as original, closing(sqlite3.connect(destination)) as target:
            original.backup(target, pages=256, progress=progress, sleep=.05)
        return verify(destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main(argv=None):
    """JSON содержит только статус, версию и количества; пути/контакты не печатаем."""
    parser = argparse.ArgumentParser(description="Копия и восстановление SQLite Energytool")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("create", help="Согласованная копия текущей SQLite")
    backup.add_argument("--output", required=True)
    check = commands.add_parser("verify", help="Проверить существующую копию без восстановления")
    check.add_argument("--source", required=True)
    restore = commands.add_parser("restore", help="Восстановить в новый файл, не затрагивая рабочий")
    restore.add_argument("--source", required=True)
    restore.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            report = verify(args.source)
        elif args.command == "create":
            report = snapshot(database_path(), args.output)
        else:
            verify(args.source)
            report = snapshot(args.source, args.output)
        print(json.dumps({"status": "ok", **report}))
        return 0
    except FileExistsError:
        reason = "destination_exists"
    except (OSError, ValueError, sqlite3.Error, ArgumentError):
        # Ошибки драйвера могут содержать пути/SQL; наружу даём только код причины.
        reason = "backup_or_schema_check_failed"
    print(json.dumps({"status": "failed", "reason": reason}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
