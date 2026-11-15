"""OpenAI-совместимый HTTP-клиент с общим дедлайном и безопасными ошибками."""

import asyncio
import json
import time
import httpx
from core_contract import CoreUnavailable

MAX_RESPONSE_BYTES = 256 * 1024


class QwenClient:
    """Клиент разделяется потоками; результат и сообщения не сохраняются в объекте."""
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def complete(self, messages: list[dict], deadline: float) -> dict:
        """Повторяем только сетевые ошибки, 429 и 5xx; 401 не лечим заменой ключа."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CoreUnavailable('Истекло время ответа ядра')
        try:
            # Синхронный контракт backend вызывается в рабочем потоке. Собственный
            # короткоживущий loop позволяет отменить даже медленно текущий HTTP-ответ.
            return asyncio.run(self._bounded(messages, remaining))
        except TimeoutError:
            raise CoreUnavailable('Истекло время ответа API') from None
        except CoreUnavailable:
            raise
        except Exception:
            raise CoreUnavailable('Не удалось обработать ответ API') from None

    async def _bounded(self, messages, remaining):
        """Один таймаут охватывает подключение, чтение всех порций и повторы."""
        async with asyncio.timeout(remaining):
            async with httpx.AsyncClient(transport=self.transport, follow_redirects=False) as client:
                return await self._complete(client, messages)

    async def _complete(self, client, messages):
        """Клиент не хранит пользовательскую историю после завершения запроса."""
        for attempt in range(self.settings.retries + 1):
            try:
                async with client.stream(
                    'POST', self.settings.completion_url,
                    headers={'Authorization': 'Bearer ' + self.settings.api_key},
                    json={'model': self.settings.model, 'messages': messages,
                          'temperature': self.settings.temperature, 'max_tokens': self.settings.max_tokens},
                    timeout=self.settings.network_seconds,
                ) as response:
                    if response.status_code in {401, 403}:
                        raise CoreUnavailable('API отклонил доступ; проверьте ключ с владельцем API')
                    retryable = response.status_code == 429 or response.status_code >= 500
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
                                raise ValueError
                            message = choice['message']
                            if message.get('tool_calls') or not isinstance(message.get('content'), str):
                                raise ValueError
                            text = message['content'].strip()
                            if text.startswith('```json\n') and text.endswith('\n```'):
                                text = text[8:-4]
                            result = json.loads(text)
                            if not isinstance(result, dict):
                                raise ValueError
                            return result
                        except (ValueError, KeyError, IndexError, TypeError, RecursionError):
                            raise CoreUnavailable('Модель вернула некорректный структурированный ответ') from None
            except httpx.TransportError:
                retryable = True
            if not retryable or attempt >= self.settings.retries:
                raise CoreUnavailable('API модели временно недоступен')
            delay = min(.5 * 2 ** attempt, 2)
            await asyncio.sleep(delay)
        raise CoreUnavailable('API модели временно недоступен')

    def close(self):
        """Соединения закрываются async-контекстом после каждого ответа."""
