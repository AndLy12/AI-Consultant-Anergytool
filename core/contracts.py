from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    role: str
    content: str


class Consultant(Protocol):
    async def answer(self, history: tuple[Message, ...]) -> dict:
        """Возвращает ответ, который приложение обязано проверить перед использованием.

        Последняя реплика истории — текущее сообщение пользователя. Cookie, данные
        сессии, окружение сервера и соединения с БД сюда не передаются. Реализация
        должна работать и в самостоятельном CSV-адаптере, без запущенного веба.
        """
        ...
