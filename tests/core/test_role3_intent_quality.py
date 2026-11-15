"""Независимые минимальные пары по намерению, только синтетические источники.

Проверки baseline сохранены без ослабления утверждений. В интеграционной
версии ожидаемые падения сняты после повторного запуска с --runxfail:
исправления подтверждены на этих синтетических входах. Модель здесь не вызывается.
"""

import pytest

from core.documents import make_chunks
from core.retrieval import SearchIndex
from core_contract import CoreRequest


@pytest.fixture
def index():
    sources = {
        'platform.md': '# Облачная платформа учёта\n\n## Как работает\nУчебный сбор показаний по арендаторам и цехам, единый кабинет приборов.',
        'solar.md': '# Солнечные электростанции\n\n## Как работает\nУчебная солнечная станция, панели и кровля, ограничения резерва.',
        'market.md': '# Выход на оптовый рынок\n\n## Как работает\nУчебный переход предприятия на оптовый рынок.',
        'checklist.md': '# Учебная подготовка\n\n## Для платформы учёта\nДля начала учёта по арендаторам подготовьте учебный список приборов.\n\n## Для солнечной станции\nСолнечные панели: для начала проекта подготовьте учебный план кровли.\n\n## Для анализа затрат и выхода на оптовый рынок\nДля начала анализа оптового рынка подготовьте учебный профиль.',
    }
    chunks = [chunk for name, text in sources.items() for chunk in make_chunks(name, [(text, None, None)], 1800)]
    return SearchIndex(tuple(chunks))


def required_sections(index, message, history=()):
    request = CoreRequest(message=message, history=list(history))
    return {hit.chunk.section for hit in index.search_request(request, 5) if hit.required}


@pytest.mark.parametrize('message', [
    'Нужен учёт по арендаторам. С чего начать?',
    'Как начать автоматический учёт по цехам?',
])
def test_positive_platform_intent_keeps_platform_checklist(index, message):
    hits = index.search_request(CoreRequest(message=message), 5)
    assert 'Для платформы учёта' in {chunk.section for chunk in index.context(hits, 14000)}


@pytest.mark.parametrize('message', [
    'Нужны солнечные панели. С чего начать?',
    'Не отказываемся от солнечной станции. С чего начать?',
])
def test_including_solar_or_double_negative_keeps_solar(index, message):
    assert 'Для солнечной станции' in required_sections(index, message)


@pytest.mark.parametrize('message', [
    'Панели не нужны, нужен учёт по арендаторам. С чего начать?',
    'Не солнечные панели, а учёт по арендаторам. С чего начать?',
    'От солнечной станции отказались. Нужен учёт по цехам, с чего начать?',
])
def test_excluded_solar_is_not_a_required_checklist(index, message):
    sections = required_sections(index, message)
    assert 'Для платформы учёта' in sections
    assert 'Для солнечной станции' not in sections


def test_assistant_topic_does_not_override_user_rejection(index):
    sections = required_sections(index, 'С чего начать?', [
        {'role': 'user', 'content': 'От солнечных панелей отказались. Нужен учёт по арендаторам.'},
        {'role': 'assistant', 'content': 'Давайте обсудим солнечную станцию и кровлю.'},
    ])
    assert 'Для платформы учёта' in sections
    assert 'Для солнечной станции' not in sections
