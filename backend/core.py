"""Точка подключения независимого ядра коллеги без доступа к аккаунтам и БД."""

import importlib
import os
from core_contract import CoreRequest, CoreResponse, CoreUnavailable


class StubCore:
    """Явная заглушка для проверки backend; не имитирует поиск или консультацию."""
    def respond(self, request: CoreRequest) -> CoreResponse:
        return CoreResponse(answer="Тестовый режим backend: ядро консультанта ещё не подключено.")


class MissingCore:
    """Отсутствие настоящего ядра приводит к техническому отказу, а не выдуманному ответу."""
    def respond(self, request: CoreRequest) -> CoreResponse:
        raise CoreUnavailable("Consultant core is not configured")


def load_core():
    """При старте выбираем заглушку или фабрику create_core из доверенной настройки."""
    if os.getenv("CORE_MODE", "real") == "stub":
        return StubCore()
    module_name = os.getenv("CORE_MODULE", "consultant_core")
    if importlib.util.find_spec(module_name) is None:
        return MissingCore()
    # Фабрика роли 2 один раз готовит поисковый индекс и клиент модели из окружения.
    # CORE_MODULE задаёт оператор сервера; пользователь чата не может его менять.
    return importlib.import_module(module_name).create_core()
