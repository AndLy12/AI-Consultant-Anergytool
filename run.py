"""Запуск API после применения миграций; настройки процесса приоритетнее .env."""

from pathlib import Path
import uvicorn
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env", override=False)
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    uvicorn.run("backend.main:create_app", factory=True, host="127.0.0.1", port=8000)
