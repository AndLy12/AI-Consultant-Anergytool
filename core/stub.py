from core.contracts import Message


class StubConsultant:
    async def answer(self, history: tuple[Message, ...]) -> dict:
        return {
            "answer": "Тестовый ответ: backend получил сообщение и историю текущего диалога. "
                      "База знаний и модель пока не подключены; консультация по продуктам недоступна.",
            "status": "stub",
            "sources": [],
            "missing_information": [],
            "warnings": ["Демонстрационная заглушка, не продуктовая консультация."],
        }
