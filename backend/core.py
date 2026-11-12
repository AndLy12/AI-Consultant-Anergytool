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
    mode = os.getenv("CORE_MODE", "real")
    if mode == "stub":
        return StubCore()
    if mode != "real":
        raise CoreUnavailable("CORE_MODE must be real or stub")
    module_name = os.getenv("CORE_MODULE", "consultant_core")
    try:
        if importlib.util.find_spec(module_name) is None:
            return MissingCore()
        module = importlib.import_module(module_name)
        factory = getattr(module, "create_core", None)
        if not callable(factory):
            raise CoreUnavailable("Core module must expose create_core()")
        # Фабрика Данька один раз готовит индекс и клиент модели из окружения.
        # Не передаём исходную ошибку наружу: в ней могут быть URL с ключами.
        core = factory()
    except CoreUnavailable:
        raise CoreUnavailable("Core initialization failed; check server configuration") from None
    except Exception:
        raise CoreUnavailable("Core initialization failed; check module dependencies and configuration") from None
    if not callable(getattr(core, "respond", None)):
        # Сохраняем опубликованный протокол Данька, не меняя core/contracts.py.
        if callable(getattr(core, "answer", None)):
            from backend.legacy_core import AsyncConsultantAdapter
            return AsyncConsultantAdapter(core)
        raise CoreUnavailable("Core object must expose respond(request) or async answer(history)")
    return core
