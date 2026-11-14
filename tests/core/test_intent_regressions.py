"""Регрессии намерения: отказ от продукта отличается от вопроса о его свойстве."""

import json

import pytest

from core.dialogue import search_query
from core.documents import make_chunks
from core.config import Settings
from core.engine import EnergytoolCore
from core.retrieval import SearchIndex
from core_contract import CoreRequest


@pytest.fixture
def index():
    """Искусственный каталог содержит и продуктовые документы, и общую подготовку."""
    texts = {
        'platform.md': '# Облачная платформа\n\nУчёт электричества по арендаторам, приборы в едином кабинете. SIM менять не нужно.',
        'solar.md': '# Солнечные электростанции\n\nСолнечные панели. Резерв требует гибридной станции.',
        'market.md': '# Выход на оптовый рынок\n\nОптовый рынок электричества.',
        'photo.md': '# Распознавание показаний\n\nФото приборов и проверка показаний.',
        'prep.md': '# Подготовка\n\n## Для платформы учёта\n\nЧисло приборов и схема учёта.\n\n## Для солнечной станции\n\nПлощадь крыши.\n\n## Для анализа затрат и выхода на оптовый рынок\n\nПрофиль потребления.',
    }
    return SearchIndex(tuple(c for name, text in texts.items()
                             for c in make_chunks(name, [(text, None, None)], 1800)))


@pytest.mark.parametrize('message,expected,excluded', [
    ('Панели не нужны, нужен учёт. С чего начать?', 'Облачная платформа', 'Солнечные'),
    ('Учёт не нужен, нужны солнечные панели. С чего начать?', 'Солнечные', 'Облачная'),
    ('Не солнечные панели, а учёт по арендаторам. С чего начать?', 'Облачная', 'Солнечные'),
    ('Отказываемся от солнечной станции. Нужен учёт.', 'Облачная', 'Солнечные'),
    ('Не рассматриваем солнечные панели и оптовый рынок. Нужен учёт.', 'Облачная', 'Солнечные'),
    ('Можно подключить приборы в единый кабинет? Солнечные станции и выход на оптовый рынок не нужны.', 'Облачная', 'Солнечные'),
])
def test_excluded_product_does_not_enter_hits_or_context(index, message, expected, excluded):
    request = CoreRequest(message=message)
    hits = index.search_request(request, 5)
    context = index.context(hits, 14000)
    assert any(expected in c.title for c in context)
    assert all(excluded not in c.title and not (
        excluded == 'Солнечные' and c.section == 'Для солнечной станции') for c in context)
    assert excluded.lower() not in search_query(request).lower()


@pytest.mark.parametrize('message,expected', [
    ('Не отказываемся от солнечной станции. Какие ограничения?', 'Солнечные'),
    ('Не можем отказаться от солнечной станции. Какие ограничения?', 'Солнечные'),
    ('Нужно менять SIM?', 'Облачная'),
    ('SIM менять не нужно?', 'Облачная'),
    ('Солнечная станция не даст резерв?', 'Солнечные'),
    ('Не только солнечные панели, но и учёт.', 'Солнечные'),
])
def test_property_negation_and_double_negation_keep_product(index, message, expected):
    assert expected in index.search_request(CoreRequest(message=message), 1)[0].chunk.title


def test_last_user_intent_wins_over_assistant_and_old_topic(index):
    request = CoreRequest(message='Что понадобится для начала?', history=[
        {'role': 'user', 'content': 'Нужны солнечные панели.'},
        {'role': 'user', 'content': 'Панели больше не рассматриваем, нужен учёт по арендаторам.'},
        {'role': 'assistant', 'content': 'Для вашей солнечной станции понадобится крыша 600 м².'},
    ])
    hits = index.search_request(request, 5)
    assert hits[0].chunk.section == 'Для платформы учёта'
    assert all('Солнечные' not in h.chunk.title for h in hits)
    assert '600' not in search_query(request)


def test_return_reactivates_excluded_product(index):
    request = CoreRequest(message='Вернёмся к первому варианту. Что потребуется?', history=[
        {'role': 'user', 'content': 'Нужна солнечная станция.'},
        {'role': 'user', 'content': 'Панели не нужны, нужен учёт.'},
    ])
    hits = index.search_request(request, 1)
    assert hits[0].chunk.section == 'Для солнечной станции' and hits[0].required


def test_explicit_reactivation_and_multiple_products(index):
    request = CoreRequest(message='Теперь нужны и солнечные панели, и учёт по арендаторам.', history=[
        {'role': 'user', 'content': 'Панели не рассматриваем.'},
    ])
    assert {h.chunk.title for h in index.search_request(request, 2)} == {
        'Солнечные электростанции', 'Облачная платформа'}


def test_ambiguous_cabinet_does_not_invent_metering_intent(index):
    hits = index.search_request(CoreRequest(message='Нужен единый кабинет для кадров.'), 5)
    assert not any(h.required for h in hits)


def test_category_reply_uses_question_topic_without_assistant_facts():
    request = CoreRequest(message='Кажется, четвёртая. Что проверить в счёте?', history=[
        {'role': 'user', 'content': 'Расходы на свет выросли.'},
        {'role': 'assistant', 'content': 'У вас мощность 600 кВт. Какая у вас ценовая категория?'},
    ])
    query = search_query(request)
    assert 'ценовые категории' in query and '600' not in query
    chunks = make_chunks('categories.md', [('# Ценовые категории\n\nЧетвёртая: почасовой учёт.', None, None)], 1800)
    hits = SearchIndex(tuple(chunks)).search_request(request, 1)
    assert hits[0].required and 'Четвёртая' in hits[0].chunk.text


def test_reranking_and_context_do_not_reintroduce_rejected_preparation(index, tmp_path):
    class Client:
        def rerank(self, query, documents, count, deadline):
            assert all('Для солнечной станции' not in doc for doc in documents)
            return list(reversed(range(len(documents))))[:count]

        def complete(self, messages, deadline):
            documents = json.loads(messages[-1]['content'])['documents']
            assert all('Солнечные' not in doc['title'] and doc['section'] != 'Для солнечной станции'
                       for doc in documents)
            return {'status': 'answered', 'answer': 'Нужны число приборов и схема учёта.', 'evidence': ['D1']}

    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder',
                   reranker_model='reranker', review_answers=False)
    result = EnergytoolCore(cfg, chunks=index.chunks, client=Client()).respond(
        CoreRequest(message='Панели не нужны, нужен учёт. С чего начать?'))
    assert result.core_status == 'answered'
