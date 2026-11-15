"""Проверка подключения ядра Данька до запуска веб-приложения.

Без --message только загружаем ядро; по контракту его фабрика может строить индекс.
С --message выполняется реальный вызов, поэтому для проверки backend без API
нужно явно задать CORE_MODE=stub. Ни ключи, ни ответы модели не выводятся.
"""

import argparse
import json
import time

from backend.core import MissingCore, StubCore, load_core
from core_contract import CoreRequest, CoreResponse


def main(argv=None):
    """Код 0 — проверка пройдена; 2 — подключение или контракт не готовы."""
    parser = argparse.ArgumentParser(description="Проверка контракта ядра консультанта")
    parser.add_argument("--message", help="Проверить настоящий ответ на указанный текст")
    args = parser.parse_args(argv)
    started = time.monotonic()
    core = None
    try:
        core = load_core()
        if isinstance(core, MissingCore):
            print(json.dumps({"status": "not_ready", "reason": "core_module_missing"}))
            return 2
        report = {"status": "ready", "mode": "stub" if isinstance(core, StubCore) else "real"}
        report["demo"] = bool(getattr(getattr(core, "settings", None), "demo", False))
        if args.message is not None:
            # Валидация совпадает с той, которую backend применяет перед записью ответа.
            result = CoreResponse.model_validate(core.respond(CoreRequest(message=args.message)))
            report.update(answer_length=len(result.answer), sources_count=len(result.sources), action=result.action)
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        print(json.dumps(report))
        return 0
    except Exception:
        # Не печатаем исключение или traceback: они могут раскрыть настройки провайдера.
        print(json.dumps({"status": "not_ready", "reason": "core_initialization_or_response_invalid"}))
        return 2
    finally:
        # Проверка фабрики тоже владеет экземпляром ядра и освобождает его ресурсы.
        close = getattr(core, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
