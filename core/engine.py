"""Общее ядро веба и платформы: запрос → поиск → модель → проверенные источники."""

import time
from threading import BoundedSemaphore
from core.config import Settings
from core.demo import DemoClient, demo_chunks
from core.dialogue import bounded_history, search_query
from core.documents import load_chunks
from core.llm import QwenClient
from core.prompts import build_messages, build_review_messages
from core.retrieval import SearchIndex
from core.validation import assemble
from core_contract import CoreRequest, CoreResponse, CoreUnavailable, ModelResponseError


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
        rerank = getattr(self.client, 'rerank', None) if self.settings.reranker_model else None
        candidate_count = max(16, self.settings.top_k * 2) if rerank else self.settings.top_k
        hits = self.index.search_request(bounded_request, candidate_count)
        if not hits:
            raw = {'status': 'insufficient_data', 'evidence': []}
            result = assemble(raw, (), bounded_request, demo=self.settings.demo,
                              truncated=truncated, secret=self.settings.api_key)
        else:
            # Один экземпляр ядра вызывается из разных диалогов: ограничение
            # одновременных запросов должно действовать и в вебе, а не только в CSV.
            if not self.model_slots.acquire(timeout=max(0, limit - time.monotonic())):
                raise CoreUnavailable('Истекло время ожидания очереди модели')
            try:
                if rerank and len(hits) > 1:
                    required = [h for h in hits if h.required]
                    hits = self.index.rerank_candidates(hits)
                    passages = [f'{h.chunk.title}\n{h.chunk.section or ""}\n{h.chunk.text}' for h in hits]
                    query = search_query(bounded_request)
                    if self.settings.api_key:
                        query = query.replace(self.settings.api_key, '[секрет удалён]')
                        passages = [p.replace(self.settings.api_key, '[секрет удалён]') for p in passages]
                    order = rerank(query, passages, min(len(hits), self.settings.top_k), limit)
                    hits = tuple(hits[i] for i in order) + tuple(h for i, h in enumerate(hits) if i not in order)
                    # Смысловой реранкер дополняет поиск. Явный список подготовки,
                    # категория и оговорка профиля не должны исчезнуть из-за его порядка.
                    required_ids = {h.chunk.chunk_id for h in required}
                    hits = tuple(required) + tuple(h for h in hits if h.chunk.chunk_id not in required_ids)
                chunks = self.index.context(hits[:self.settings.top_k], self.settings.context_chars)
                if not chunks:
                    return assemble({'status': 'insufficient_data'}, (), bounded_request,
                                    demo=self.settings.demo, truncated=truncated, secret=self.settings.api_key)
                messages = build_messages(bounded_request, history, chunks, self.settings.api_key)
                # Исправление структуры и оснований имеет тот же дедлайн и слот
                # параллельности. Ошибки доступа и сети здесь не повторяем.
                for attempt in range(2):
                    try:
                        raw = self.client.complete(messages, limit)
                        result = assemble(raw, chunks, bounded_request, demo=self.settings.demo,
                                          truncated=truncated, secret=self.settings.api_key)
                        break
                    except ModelResponseError:
                        if attempt == 1 or time.monotonic() >= limit:
                            raise
                        messages = [dict(messages[0], content=messages[0]['content'] +
                            '\nПредыдущая попытка не прошла проверку. Верни только JSON по схеме. '
                            'Для answered обязательны короткий answer и evidence с существующими '
                            'короткими ID из documents. Не добавляй поля. '
                            'При отсутствии основания используй insufficient_data.'), messages[1]]
                if self.settings.review_answers and result.core_status == 'answered' and not self.settings.demo:
                    # У проверки собственная часть оставшегося бюджета. При её
                    # техническом сбое сохраняем уже валидную консультацию.
                    remaining = limit - time.monotonic()
                    if remaining > 2:
                        review_limit = time.monotonic() + min(35, remaining / 2)
                        try:
                            reviewer = getattr(self.client, 'review', self.client.complete)
                            checked = reviewer(build_review_messages(messages, raw), review_limit)
                            final = checked.get('result', checked)
                            if isinstance(final, dict) and final.get('status') == 'answered' and checked.get('follow_up_question'):
                                final = dict(final, question=checked['follow_up_question'])
                            result = assemble(final, chunks, bounded_request, demo=False,
                                              truncated=truncated, secret=self.settings.api_key)
                        except CoreUnavailable:
                            result.warnings.append('Дополнительная проверка ответа не завершена.')
            finally:
                self.model_slots.release()
        if time.monotonic() >= limit:
            raise CoreUnavailable('Истекло время ответа ядра')
        return result

    def close(self):
        """Освобождаем соединения при завершении самостоятельного запуска."""
        self.client.close()
