"""OpenAI-совместимый HTTP-клиент с общим дедлайном и безопасными ошибками."""

import asyncio
import json
import time
import httpx
from core_contract import CoreUnavailable, ModelResponseError, ProviderError
from core.validation import AnswerReview

MAX_RESPONSE_BYTES = 256 * 1024


class QwenClient:
    """Клиент разделяется потоками; результат и сообщения не сохраняются в объекте."""
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport
        self.use_thinking_option = True
        self.use_reasoning_effort = True
        self.use_json_mode = True
        self.json_format = 'json_schema'

    def rerank(self, query, documents, count, deadline):
        """Индексы из официального /rerank; при сбое сохраняем локальный порядок.

        Поиск не повторяет сетевой запрос и оставляет основной бюджет генерации.
        Частичный валидный результат дополняется исходным порядком в ядре.
        """
        async def request():
            budget = min(5.0, (deadline - time.monotonic()) / 5)
            if budget <= 0:
                return []
            async with asyncio.timeout(budget):
                async with httpx.AsyncClient(transport=self.transport, follow_redirects=False) as client:
                    url = self.settings.completion_url.removesuffix('/chat/completions') + '/rerank'
                    response = await client.post(url, headers={'Authorization': 'Bearer ' + self.settings.api_key},
                        json={'model': self.settings.reranker_model, 'query': query,
                              'documents': documents, 'top_n': count}, timeout=budget)
                    response.raise_for_status()
                    body = response.json()
                    entries = body.get('results', body.get('data'))
                    if not isinstance(entries, list):
                        return []
                    indices = []
                    for item in entries[:count]:
                        if not isinstance(item, dict):
                            return []
                        index = item.get('index')
                        if type(index) is not int or not 0 <= index < len(documents) or index in indices:
                            return []
                        indices.append(index)
                    return indices
        try:
            return asyncio.run(request())
        except (TimeoutError, httpx.HTTPError, ValueError, AttributeError):
            return []

    def complete(self, messages: list[dict], deadline: float, *, response_schema=None) -> dict:
        """Повторяем только сетевые ошибки, 429 и 5xx; 401 не лечим заменой ключа."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CoreUnavailable('Истекло время ответа ядра')
        try:
            # Синхронный контракт backend вызывается в рабочем потоке. Собственный
            # короткоживущий loop позволяет отменить даже медленно текущий HTTP-ответ.
            return asyncio.run(self._bounded(messages, remaining, response_schema))
        except TimeoutError:
            raise ProviderError('timeout', 'Истекло время ответа API') from None
        except CoreUnavailable:
            raise
        except Exception:
            raise CoreUnavailable('Не удалось обработать ответ API') from None

    def review(self, messages, deadline):
        """Схема обязательного результата не позволяет закончить ответ одними замечаниями."""
        return self.complete(messages, deadline, response_schema=AnswerReview.model_json_schema())

    async def _bounded(self, messages, remaining, response_schema=None):
        """Один таймаут охватывает подключение, чтение всех порций и повторы."""
        async with asyncio.timeout(remaining):
            async with httpx.AsyncClient(transport=self.transport, follow_redirects=False) as client:
                return await self._complete(client, messages, response_schema)

    async def _complete(self, client, messages, response_schema=None):
        """Клиент не хранит пользовательскую историю после завершения запроса."""
        payload = {'model': self.settings.model, 'messages': messages,
                   'temperature': self.settings.temperature, 'max_tokens': self.settings.max_tokens}
        if self.use_thinking_option:
            payload['chat_template_kwargs'] = {'enable_thinking': False}
        if self.use_reasoning_effort:
            payload['reasoning_effort'] = 'none'
        if self.use_json_mode:
            payload['response_format'] = {'type': self.json_format}
            if self.json_format == 'json_schema':
                payload['response_format']['json_schema'] = {
                    'name': 'response', 'strict': False, 'schema': response_schema or {'type': 'object'}}

        for attempt in range(self.settings.retries + 1):
            try:
                async with client.stream(
                    'POST', self.settings.completion_url,
                    headers={'Authorization': 'Bearer ' + self.settings.api_key},
                    json=payload,
                    timeout=self.settings.network_seconds,
                ) as response:
                    # Часть совместимых прокси отвергает расширения шаблона Qwen.
                    # При явном указании прокси отключаем только названное поле.
                    # Без диагностики сначала снимаем JSON-режим, сохраняя настройку
                    # мышления. Все попытки разделяют исходный дедлайн; тело ошибки
                    # читаем с ограничением размера и не выводим в журнал.
                    if response.status_code in {400, 422} and (
                            'reasoning_effort' in payload or 'chat_template_kwargs' in payload or 'response_format' in payload):
                        error = bytearray()
                        async for part in response.aiter_bytes():
                            error.extend(part[:max(0, 8192 - len(error))])
                            if len(error) >= 8192:
                                break
                        detail = error.decode('utf-8', errors='replace')
                        lowered = detail.lower()
                        if any(marker in lowered for marker in (
                                'exceed_context', 'context_length_exceeded', 'maximum context length',
                                'exceeds the available context', 'context window', 'too many tokens',
                                'prompt is too long')):
                            # Длина запроса не определяет возможности API. Нельзя
                            # из-за длинной истории отключать формат и управление
                            # мышлением для всех последующих диалогов этого клиента.
                            raise ProviderError('context_length', 'Контекст запроса превышает лимит модели')
                        options = [name for name in ('response_format', 'reasoning_effort', 'chat_template_kwargs')
                                   if name in payload]
                        rejected = next((name for name in options if name in detail), options[0])
                        flag = {'response_format': 'use_json_mode',
                                'reasoning_effort': 'use_reasoning_effort',
                                'chat_template_kwargs': 'use_thinking_option'}[rejected]
                        if rejected == 'response_format' and payload['response_format']['type'] == 'json_schema':
                            # При отсутствии поддержки схемы остаётся json_object.
                            # Решение принимаем по отправленному
                            # payload, чтобы параллельный запрос не отключил уже
                            # согласованный другой потоками режим.
                            self.json_format = 'json_object'
                        else:
                            setattr(self, flag, False)
                        await response.aclose()
                        return await self._complete(client, messages, response_schema)
                    if response.status_code in {401, 403}:
                        raise ProviderError('access_denied', 'API отклонил доступ; проверьте ключ с владельцем API')
                    retryable = response.status_code == 429 or response.status_code >= 500
                    category = 'rate_limit' if response.status_code == 429 else 'upstream_5xx'
                    if not retryable:
                        if response.status_code != 200:
                            raise CoreUnavailable('API вернул неподдерживаемый HTTP-статус')
                        content = bytearray()
                        async for part in response.aiter_bytes():
                            content.extend(part)
                            if len(content) > MAX_RESPONSE_BYTES:
                                raise CoreUnavailable('Ответ API превышает допустимый размер')
                        try:
                            body = json.loads(content)
                            choice = body['choices'][0]
                            if choice.get('finish_reason') == 'length':
                                raise ModelResponseError('Модель исчерпала лимит токенов', 'truncated')
                            message = choice['message']
                            if message.get('tool_calls') or not isinstance(message.get('content'), str):
                                raise ValueError
                            text = message['content'].strip()
                            # Удаляем только завершённый служебный блок и внешнюю
                            # Markdown-обёртку. Произвольный JSON внутри текста не ищем.
                            if text.startswith('<think>'):
                                _, closing, text = text.partition('</think>')
                                if not closing:
                                    raise ValueError
                                text = text.strip()
                            if text.startswith('```') and text.endswith('```'):
                                opening, separator, content_text = text.partition('\n')
                                if not separator or opening.strip().lower() not in {'```', '```json'}:
                                    raise ValueError
                                text = content_text[:-3].strip()
                            result = json.loads(text)
                            if not isinstance(result, dict):
                                raise ValueError
                            return result
                        except (ValueError, KeyError, IndexError, TypeError, RecursionError):
                            raise ModelResponseError('Модель вернула некорректный структурированный ответ') from None
            except httpx.TransportError as exc:
                retryable = True
                category = 'timeout' if isinstance(exc, httpx.TimeoutException) else 'network'
            if not retryable or attempt >= self.settings.retries:
                raise ProviderError(category, 'API модели временно недоступен')
            delay = min(.5 * 2 ** attempt, 2)
            await asyncio.sleep(delay)
        raise CoreUnavailable('API модели временно недоступен')

    def close(self):
        """Соединения закрываются async-контекстом после каждого ответа."""
