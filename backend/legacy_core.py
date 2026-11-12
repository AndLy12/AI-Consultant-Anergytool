"""Подключение async answer(history) Данька без изменения каталога core."""

import asyncio
import os
from concurrent.futures import TimeoutError as FutureTimeout
from threading import Event, Thread

from backend.schemas import Answer
from core.contracts import Message
from core_contract import CoreRequest, CoreResponse, CoreUnavailable, Source


class AsyncConsultantAdapter:
    """Вызывается из синхронного обработчика в рабочем потоке FastAPI."""

    def __init__(self, consultant, timeout=None):
        self.consultant = consultant
        self.timeout = float(timeout if timeout is not None else os.getenv("CORE_TIMEOUT_SECONDS", "120"))
        if not 0 < self.timeout <= 120:
            raise CoreUnavailable("CORE_TIMEOUT_SECONDS must be >0 and <=120")
        # Один цикл нужен для повторного использования async HTTP-клиента ядра.
        # Отдельный поток позволяет синхронным обработчикам ожидать его результат.
        self.loop = asyncio.new_event_loop()
        ready = Event()
        def run():
            asyncio.set_event_loop(self.loop)
            ready.set()
            self.loop.run_forever()
        self.thread = Thread(target=run, name="consultant-async-core", daemon=True)
        self.thread.start()
        ready.wait()

    def respond(self, request: CoreRequest) -> CoreResponse:
        """Добавляем текущую реплику к истории один раз и проверяем результат."""
        history = tuple(Message(role=t.role, content=t.content) for t in request.history)
        history += (Message(role="user", content=request.message),)

        async def invoke():
            # Отмена ограничивает корректный async-вызов. Сетевые таймауты и
            # отсутствие блокирующего кода в event loop обеспечивает ядро.
            return await asyncio.wait_for(self.consultant.answer(history), self.timeout)

        future = asyncio.run_coroutine_threadsafe(invoke(), self.loop)
        try:
            result = Answer.model_validate(future.result(timeout=self.timeout + 1))
        except FutureTimeout:
            future.cancel()
            raise CoreUnavailable("Async core timed out") from None
        except Exception:
            raise CoreUnavailable("Async core failed or returned an invalid response") from None
        if result.status == "stub":
            # Тестовый ответ разрешён только через явный CORE_MODE=stub.
            raise CoreUnavailable("Real core returned a stub response")
        action = {"answered": "answer", "clarification_needed": "clarify",
                  "insufficient_data": "clarify", "out_of_scope": "out_of_scope"}[result.status]
        return CoreResponse(answer=result.answer, action=action, core_status=result.status,
            missing_information=result.missing_information, warnings=result.warnings,
            sources=[Source(document_id=s.document_id, chunk_id=s.chunk_id, title=s.title,
                            excerpt=s.quote, section=s.section, page=s.page) for s in result.sources])

    def close(self):
        """Освобождаем клиент ядра и поток при остановке единственного backend."""
        if not self.loop.is_running():
            return
        async def cleanup():
            close = getattr(self.consultant, "aclose", None)
            if callable(close):
                await close()
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        try:
            asyncio.run_coroutine_threadsafe(cleanup(), self.loop).result(timeout=2)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=2)
            if not self.thread.is_alive():
                self.loop.close()
