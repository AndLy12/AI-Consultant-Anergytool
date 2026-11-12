"""Вымышленные материалы и предсказуемая заглушка: только явный CORE_DEMO=true."""

import json
from core.documents import make_chunks

DEMO_NAME = 'Учебный стенд Альфа'
DEMO_TEXT = '''# Учебный стенд Альфа — вымышленные сведения

Учебный стенд Альфа показывает демонстрационный расход воды по дням. Это вымышленный пример для проверки программы, не описание продукта Energytool.

Демонстрационный отчёт стенда Альфа доступен за выбранный период до семи дней. Данные отчёта вымышленные и не подтверждают возможности Energytool.

В учебном стенде Альфа экспорт отчёта выполняется в CSV. Вымышленный пример предназначен только для проверки поиска и ссылок.
'''


def demo_chunks(size: int):
    """Оригинальные документы команды не нужны и не подменяются этим примером молча."""
    return tuple(make_chunks(DEMO_NAME + '.md', [(DEMO_TEXT, None, None)], size))


class DemoClient:
    """Без сети выбираем первый переданный фрагмент; качество LLM так не оценивается."""
    def complete(self, messages, deadline):
        data = json.loads(messages[-1]['content'])
        documents = data['documents']
        return {
            'status': 'answered' if documents else 'insufficient_data',
            'evidence': [{'chunk_id': documents[0]['chunk_id'], 'quote': documents[0]['text']}] if documents else [],
            'question': None, 'missing_information': [], 'summary_draft': None,
        }

    def close(self):
        """Сетевые соединения в этом режиме не создаются."""
