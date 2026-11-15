"""Общее ядро веба и платформы: запрос → поиск → модель → проверенные источники."""

import time
from threading import BoundedSemaphore
from core.config import Settings
from core.demo import DemoClient, demo_chunks
from core.dialogue import bounded_history, search_query
from core.documents import load_chunks
from core.llm import QwenClient
from core.prompts import build_messages
from core.retrieval import SearchIndex
from core.validation import assemble
from core_contract import CoreRequest, CoreResponse, CoreUnavailable


class EnergytoolCore:
    """Объект содержит только настройки, неизменяемую базу, индекс и HTTP-клиент.

    Ни история, ни контакты, ни черновик заявки не сохраняются между respond.
    Это позволяет одному экземпляру обслуживать независимые диалоги.
    """
    def __init__(self, settings: Settings, chunks=None, client=None, deadline=None):
        self.settings = settings
        if not settings.demo:
            settings.validate_provider()
        self.chunks = tuple(chunks) if chunks is not None else (
            demo_chunks(settings.chunk_chars) if settings.demo else load_chunks(settings, deadline))
        if not self.chunks:
            raise CoreUnavailable('Нет фрагментов для поиска')
        self.index = SearchIndex(self.chunks)
        if deadline is not None and time.monotonic() >= deadline:
            raise CoreUnavailable('Истекло время построения индекса')
        self.client = client or (DemoClient() if settings.demo else QwenClient(settings))
        self.model_slots = BoundedSemaphore(settings.max_workers)

    def respond(self, request: CoreRequest, *, deadline: float | None = None) -> CoreResponse:
        """Один запрос не пишет в БД и не исполняет предложения модели как действия."""
        request = CoreRequest.model_validate(request)
        limit = time.monotonic() + self.settings.request_seconds
        if deadline is not None:
            limit = min(limit, deadline)
        if time.monotonic() >= limit:
            raise CoreUnavailable('Истекло время ответа ядра')
        history, truncated = bounded_history(request, self.settings.history_chars)
        # Поиск тоже использует только ограниченную историю, а не весь накопленный архив.
        bounded_request = CoreRequest(message=request.message, history=history)
        hits = self.index.search(search_query(bounded_request), self.settings.top_k)
        chunks = self.index.context(hits, self.settings.context_chars)
        if not chunks:
            raw = {'status': 'insufficient_data', 'evidence': []}
        else:
            messages = build_messages(bounded_request, history, chunks, self.settings.api_key)
            # Один экземпляр ядра вызывается из разных диалогов: ограничение
            # одновременных запросов должно действовать и в вебе, а не только в CSV.
            if not self.model_slots.acquire(timeout=max(0, limit - time.monotonic())):
                raise CoreUnavailable('Истекло время ожидания очереди модели')
            try:
                raw = self.client.complete(messages, limit)
            finally:
                self.model_slots.release()
        if time.monotonic() >= limit:
            raise CoreUnavailable('Истекло время ответа ядра')
        return assemble(raw, chunks, bounded_request, demo=self.settings.demo,
                        truncated=truncated, secret=self.settings.api_key)

    def close(self):
        """Освобождаем соединения при завершении самостоятельного запуска."""
        self.client.close()
