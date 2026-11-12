"""Настройки ядра из серверного окружения, без чтения пользовательских сообщений."""

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from core_contract import CoreUnavailable


def first_env(*names: str) -> str:
    """Платформенные имена имеют тот же приоритет, что указан в инструкции."""
    return next((os.environ[name].strip() for name in names if os.environ.get(name, '').strip()), '')


def number(name: str, default: str, minimum: float, maximum: float, integer=False):
    """Ошибку конфигурации показываем по имени поля, не раскрывая его значение."""
    try:
        value = int(os.getenv(name, default)) if integer else float(os.getenv(name, default))
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError
        return value
    except ValueError:
        raise CoreUnavailable(f'Некорректная настройка {name}') from None


@dataclass(frozen=True)
class Settings:
    """Неизменяемая конфигурация; секрет исключён из repr и диагностических отчётов."""
    data_dir: Path
    kb_path: Path | None = None
    demo: bool = False
    base_url: str = field(default='', repr=False)
    api_key: str = field(default='', repr=False)
    model: str = ''
    temperature: float = 0.1
    max_tokens: int = 1200
    top_k: int = 5
    chunk_chars: int = 1800
    context_chars: int = 14000
    history_chars: int = 16000
    request_seconds: float = 60
    network_seconds: float = 15
    retries: int = 2
    max_workers: int = 4

    @classmethod
    def from_environment(cls):
        """Режим демонстрации включается только CORE_DEMO=true, не автоматически."""
        mode = os.getenv('CORE_DEMO', 'false').lower()
        if mode not in {'true', 'false'}:
            raise CoreUnavailable('CORE_DEMO должен быть true или false')
        data = Path(os.getenv('DATA_DIR', 'data')).expanduser().resolve()
        configured = os.getenv('CORE_KB_PATH', '').strip()
        kb = Path(configured).expanduser().resolve() if configured else None
        settings = cls(
            data_dir=data, kb_path=kb, demo=mode == 'true',
            base_url=first_env('LLM_BASE_URL', 'OPENAI_BASE_URL'),
            api_key=first_env('LLM_API_KEY', 'OPENAI_API_KEY'),
            model=first_env('LLM_MODEL', 'OPENAI_MODEL', 'GEN_MODEL'),
            temperature=number('TEMPERATURE', '0.1', 0, 2),
            max_tokens=number('MAX_TOKENS', '1200', 1, 16000, True),
            top_k=number('TOP_K', '5', 1, 20, True),
            chunk_chars=number('CORE_CHUNK_CHARS', '1800', 300, 7000, True),
            context_chars=number('CORE_CONTEXT_CHARS', '14000', 2000, 60000, True),
            history_chars=number('CORE_HISTORY_CHARS', '16000', 1000, 60000, True),
            request_seconds=number('CORE_TIMEOUT_SECONDS', '60', 1, 120),
            network_seconds=number('LLM_TIMEOUT_SECONDS', '15', .1, 30),
            retries=number('LLM_MAX_RETRIES', '2', 0, 3, True),
            max_workers=number('MAX_WORKERS', '4', 1, 16, True),
        )
        if os.getenv('CHUNKER', 'sections') != 'sections':
            raise CoreUnavailable('Этот baseline поддерживает CHUNKER=sections')
        if not settings.demo:
            settings.validate_provider()
        return settings

    def validate_provider(self):
        """Адрес и ключ доверяем только окружению, а не результату модели."""
        if not self.base_url or not self.api_key or not self.model:
            raise CoreUnavailable('Нужны LLM_BASE_URL, LLM_API_KEY и LLM_MODEL либо их платформенные аналоги')
        try:
            url = urlsplit(self.base_url)
            valid = (url.scheme in {'http', 'https'} and url.hostname and not url.username
                     and not url.password and not url.query and not url.fragment)
            if not valid or any(c in self.api_key for c in '\r\n'):
                raise ValueError
        except ValueError:
            raise CoreUnavailable('Некорректная конфигурация провайдера') from None

    @property
    def completion_url(self):
        """Не добавляем /v1: базовый путь задаёт владелец API."""
        base = self.base_url.rstrip('/')
        return base if base.endswith('/chat/completions') else base + '/chat/completions'
