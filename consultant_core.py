"""Публичная фабрика, которую загружает backend и тот же solution.py."""

from core.config import Settings
from core.engine import EnergytoolCore


def create_core(settings=None, *, deadline=None):
    """Один раз читаем базу и строим индекс; сервер и веб-БД не запускаем."""
    return EnergytoolCore(settings or Settings.from_environment(), deadline=deadline)
