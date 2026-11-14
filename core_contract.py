"""Общий контракт v1 для backend и ядра; корневой файл включается в платформенный ZIP.

Зависит только от Pydantic и стандартной библиотеки. Не импортирует веб-приложение
или БД, поэтому solution.py может использовать его самостоятельно.
"""
from typing import Literal, Protocol
from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    # Отклоняем неожиданные поля и строки из одних пробелов до сохранения результата.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Turn(ContractModel):
    """Реплика доверенной истории; публичный клиент не назначает роли самостоятельно."""
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10000)


class Source(ContractModel):
    """Использованный фрагмент базы с устойчивыми ID, без абсолютных путей сервера."""
    document_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    # Цитата до 8000 символов сохраняется целиком по существующему контракту Данька.
    excerpt: str = Field(min_length=1, max_length=8000)
    section: str | None = Field(default=None, max_length=500)
    page: int | None = Field(default=None, ge=1)


class CoreRequest(ContractModel):
    # История не включает текущую реплику. Пароли, токены и права аккаунта сюда не входят.
    message: str = Field(min_length=1, max_length=10000)
    history: list[Turn] = Field(default_factory=list)


class CoreResponse(ContractModel):
    # action — подсказка для сценария, не разрешение на запись в БД.
    # summary_draft нужно показать пользователю; он сам подтверждает итоговую заявку.
    answer: str = Field(min_length=1, max_length=10000)
    sources: list[Source] = Field(default_factory=list, max_length=30)
    action: Literal["answer", "clarify", "out_of_scope", "offer_application"] = "answer"
    summary_draft: str | None = Field(default=None, max_length=10000)
    # Диагностика исходного async-протокола сохраняется, а не теряется при адаптации.
    core_status: Literal["answered", "clarification_needed", "insufficient_data", "out_of_scope"] | None = None
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class ConsultantCore(Protocol):
    """Ядро не хранит контекст клиента между вызовами; история приходит в каждом запросе."""
    def respond(self, request: CoreRequest) -> CoreResponse: ...


class CoreUnavailable(Exception):
    """Технический сбой ядра; сообщение исключения не должно содержать секретов."""


class ProviderError(CoreUnavailable):
    """Код для агрегированного отчёта без ключей и текста запросов."""

    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


class ModelResponseError(CoreUnavailable):
    """Ответ модели допускает одну коррекцию формата или выбора оснований."""

    def __init__(self, message, category='model_response'):
        super().__init__(message)
        self.category = category
