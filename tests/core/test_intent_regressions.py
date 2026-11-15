"""Регрессии намерения: отказ от продукта отличается от вопроса о его свойстве."""

import json

import pytest

from core.dialogue import search_intent, search_query
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
    ('От солнечной станции не отказались. Какие ограничения?', 'Солнечные'),
    ('От солнечной станции не можем отказаться. Какие ограничения?', 'Солнечные'),
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


def test_preparation_for_metering_keeps_platform_in_genitive_case(index):
    request = CoreRequest(message='Для учёта планируем до восьмидесяти приборов. Какие сведения собрать?')
    hits = index.search_request(request, 5)
    assert any(h.required and h.chunk.section == 'Для платформы учёта' for h in hits)
    assert all('Солнечные' not in h.chunk.title for h in hits)


@pytest.mark.parametrize('message', [
    'Для учёта воды какие сведения собрать?',
    'Для учёта реактивной энергии какие данные нужны?',
    'В нашей системе учёта поддерживается выгрузка?',
])
def test_genitive_case_does_not_assign_other_resource_or_unknown_system(message):
    assert 'облачная платформа' not in search_intent(CoreRequest(message=message)).active


@pytest.mark.parametrize('message', [
    'Солнечные панели не требуют очистки?',
    'Солнечная станция не требует ремонта?',
    'Солнечная станция не нуждается в ежегодной настройке?',
    'Панели не надо обслуживать?',
    'Панели не нужно очищать?',
    'От солнечной станции не планируем отказываться.',
])
def test_non_reflexive_requirement_is_a_property_not_product_refusal(index, message):
    intent = search_intent(CoreRequest(message=message))
    assert 'солнечные электростанции' in intent.active
    assert 'солнечные электростанции' not in intent.excluded
    assert any('Солнечные' in hit.chunk.title for hit in index.search_request(CoreRequest(message=message), 5))


@pytest.mark.parametrize('message', [
    'Панели не требуются.', 'Солнечная станция не требуется.',
    'Нам не нужны панели.', 'Солнечная станция нам не нужна.',
])
def test_reflexive_requirement_still_excludes_unwanted_product(message):
    intent = search_intent(CoreRequest(message=message))
    assert 'солнечные электростанции' in intent.excluded
    assert 'солнечные электростанции' not in intent.active


def test_report_recipient_synonym_finds_rso_without_claiming_product():
    chunks = make_chunks('platform.md', [('# Облачная платформа\n\n## Отчёты в РСО\n\n'
        'Экспорт отчёта в ресурсоснабжающую организацию.', None, None)], 1800)
    chunks += make_chunks('privacy.md', [('# Конфиденциальность\n\n'
        'Придётся ли передавать поставщику персональные данные о ресурсах?', None, None)], 1800)
    request = CoreRequest(message='Наша отчётность отправляется поставщику ресурсов?')
    assert search_intent(request).active == ()
    assert SearchIndex(tuple(chunks)).search_request(request, 1)[0].chunk.section == 'Отчёты в РСО'
    assert 'РСО' not in search_query(CoreRequest(message='Нужен отчёт о состоянии крыши.'))


def test_report_synonym_does_not_reintroduce_explicitly_excluded_platform(index):
    request = CoreRequest(message='Облачная платформа не нужна. Отчёт поставщику передавать самим?')
    hits = index.search_request(request, 5)
    assert 'облачная платформа' in search_intent(request).excluded
    assert not any('Облачная' in h.chunk.title for h in hits)


def test_explicit_exclusion_does_not_add_rejected_product_checklist(index):
    request = CoreRequest(message='Панели исключены из нашего проекта. Какие данные нужны для учёта по цехам?')
    intent = search_intent(request)
    assert intent.active == ('облачная платформа',)
    assert 'солнечные электростанции' in intent.excluded
    context = index.context(index.search_request(request, 5), 14000)
    assert all('Солнечные' not in c.title and c.section != 'Для солнечной станции' for c in context)


@pytest.mark.parametrize('message', [
    'Солнечная станция не исключена. Какие условия?',
    'Панели исключают перебои в питании?',
])
def test_negated_exclusion_and_exclusion_as_property_keep_product(message):
    intent = search_intent(CoreRequest(message=message))
    assert 'солнечные электростанции' in intent.active
    assert 'солнечные электростанции' not in intent.excluded


@pytest.mark.parametrize('message', [
    'Солнечная станция на 700 кВт исключена из рассмотрения?',
    'Панели исключены из типовых проектов?',
])
def test_question_about_exclusion_is_not_a_decision_to_refuse(index, message):
    request = CoreRequest(message=message)
    intent = search_intent(request)
    assert 'солнечные электростанции' in intent.active
    assert 'солнечные электростанции' not in intent.excluded
    assert any('Солнечные' in h.chunk.title for h in index.search_request(request, 5))


@pytest.mark.parametrize('noun', ['продукту', 'решению'])
def test_return_to_first_product_or_solution_resolves_original_topic(index, noun):
    request = CoreRequest(message=f'Вернёмся к первому {noun}. Что подготовить?', history=[
        {'role': 'user', 'content': 'Хотим солнечные панели.'},
        {'role': 'user', 'content': 'Пока отдельно обсудим учёт.'},
    ])
    assert search_intent(request).active == ('солнечные электростанции',)
    assert index.search_request(request, 5)[0].chunk.section == 'Для солнечной станции'


def test_regional_adjective_recovers_named_source_with_power_follow_up():
    chunks = make_chunks('regions.md', [('# Регионы\n\nКамчатка: применимость зависит от энергосистемы.', None, None)], 1800)
    chunks += make_chunks('power.md', [('# Мощность\n\nПрисоединённая мощность 30 МВт.', None, None)], 1800)
    request = CoreRequest(message='Присоединённая мощность 30 МВт.', history=[
        {'role': 'user', 'content': 'Предприятие в Камчатском крае.'}])
    hits = SearchIndex(tuple(chunks)).search_request(request, 3)
    assert any('Камчатка' in h.chunk.text for h in hits)
    assert 'Камчатка' not in search_query(CoreRequest(message='Нужен только учёт в Камчатском крае.'))


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


def test_sufficiency_question_keeps_complete_preparation_requirements():
    chunks = make_chunks('losses.md', [('# Поиск коммерческих потерь\n\nАрхив не менее года.', None, None)], 1800)
    chunks += make_chunks('prep.md', [('# Подготовка\n\n## Для поиска коммерческих потерь\n\n'
                                      'Идентификатор канала, суточные данные день/ночь.', None, None)], 1800)
    request = CoreRequest(message='Архив за два года. Таких данных хватит?', history=[
        {'role': 'user', 'content': 'Хотим находить потери.'},
    ])
    hits = SearchIndex(tuple(chunks)).search_request(request, 1)
    assert hits[0].required and 'Идентификатор канала' in hits[0].chunk.text


def test_reactive_energy_question_is_not_forced_to_cloud_platform(index):
    chunks = make_chunks('reactive.md', [('# Реактивная энергия\n\nУчёт реактивной энергии.', None, None)], 1800)
    hits = SearchIndex((*index.chunks, *chunks)).search_request(
        CoreRequest(message='Что такое учёт реактивной энергии?'), 1)
    assert hits[0].chunk.title == 'Реактивная энергия'


def test_water_reading_verb_selects_recognition_instead_of_electricity_platform(index):
    hits = index.search_request(CoreRequest(message='У нас учёт воды. Как распознавать показания?'), 1)
    assert hits[0].chunk.title == 'Распознавание показаний'


def test_nested_preparation_does_not_expand_to_other_products(index):
    chunks = make_chunks('nested-prep.md', [('# Подготовка\n\n## Какие сведения могут понадобиться\n\n'
        'Состав зависит от продукта.\n\n### Для первичного понимания задачи\n\nРегион и мощность.\n\n'
        '### Для платформы учёта\n\nКоличество и тип приборов.\n\n'
        '### Для солнечной станции\n\nПлощадь кровли.', None, None)], 1800)
    current = SearchIndex((*index.chunks, *chunks))
    request = CoreRequest(message='Нужен учёт по арендаторам. Что понадобится?')
    hits = current.search_request(request, 5)
    context = current.context(hits, 14000)
    assert any(c.text == 'Количество и тип приборов.' for c in context)
    assert all(c.text != 'Площадь кровли.' and c.text != 'Регион и мощность.' for c in context)
    assert all('Солнечные' not in h.chunk.title for h in hits)


@pytest.mark.parametrize('message,history,sections', [
    ('Для офиса с одним прибором есть минимальный порог парка?', [], ['Облачная платформа учёта']),
    ('Уточни показатели качества для этих двух групп отдельно.',
     [{'role': 'user', 'content': 'Хотим принимать фото водяных счётчиков.'},
      {'role': 'user', 'content': 'Есть ещё электрические приборы.'}], ['Точность']),
    ('Расскажи про солнечный резерв.', [], ['Если пропадёт электричество, станция обеспечит объект']),
])
def test_specific_product_facts_precede_generic_sections(message, history, sections):
    """Число приборов, качество фото и резерв требуют своих условий из базы."""
    from core.documents import make_chunks
    from core.retrieval import SearchIndex
    chunks = (
        make_chunks('scope.md', [('# Область применения продуктов и ограничения\n\n'
            '## Облачная платформа учёта\n\nМинимального количества приборов учёта нет.', None, None)], 1800)
        + make_chunks('photo.md', [('# Распознавание показаний приборов учёта по фотографии\n\n'
            '## Точность\n\nЭлектричество более 95 процентов, вода более 90 процентов.\n\n'
            '## Кому подходит\n\nПриборы разных групп.', None, None)], 1800)
        + make_chunks('solar.md', [('# Солнечные электростанции под ключ\n\n'
            '## Если пропадёт электричество, станция обеспечит объект\n\n'
            'Сетевая станция отключается с сетью. Гибридная конфигурация требует накопителей.\n\n'
            '## Сколько это стоит\n\nСтоимость солнечного резерва индивидуальна.', None, None)], 1800)
        + make_chunks('power.md', [('Минимальный порог мощности для офиса, парка и одного прибора.', None, None)], 1800))
    hits = SearchIndex(tuple(chunks)).search_request(CoreRequest(message=message, history=history), 1)
    assert hits[0].chunk.section in sections
    assert hits[0].required


def test_scope_section_of_excluded_product_cannot_be_required():
    """Общий справочник не возвращает раздел исключённого продукта по его свойству."""
    chunks = make_chunks('scope.md', [('# Область применения продуктов и ограничения\n\n'
        '## Облачная платформа учёта\n\nМинимального количества приборов учёта нет.', None, None)], 1800)
    request = CoreRequest(message='Учёт не нужен. Есть минимальное количество приборов?',
                          history=[])
    assert not SearchIndex(tuple(chunks)).search_request(request)


def test_quality_anchor_does_not_override_solar_question():
    """Качество панелей не должно закреплять таблицу точности распознавания."""
    chunks = make_chunks('photo.md', [('# Распознавание показаний приборов учёта по фотографии\n\n'
        '## Точность\n\nПоказания более 95 процентов.', None, None)], 1800)
    chunks += make_chunks('solar.md', [('# Солнечные электростанции под ключ\n\n'
        '## Оборудование\n\nКачество панелей зависит от поставщика.', None, None)], 1800)
    hits = SearchIndex(tuple(chunks)).search_request(CoreRequest(message='Какое качество солнечных панелей?'))
    assert hits
    assert all('распознавание' not in h.chunk.title.lower() for h in hits)


@pytest.mark.parametrize('phrase', ['облачного учёта', 'облачным учётом', 'облачный учёт'])
def test_cloud_metering_inflections_keep_product(phrase):
    """Падеж продукта не должен оставлять запрос без активной темы."""
    assert search_intent(CoreRequest(message=f'Есть ли обследование при {phrase}?')).active == ('облачная платформа',)


def test_retrieval_normalizes_compound_terms():
    """Слитные названия услуги и кода совпадают с написанием в документах."""
    from core.retrieval import tokens
    assert tokens('энергообследование') == tokens('энергетическое обследование')
    assert tokens('штрихкод') == tokens('штрих-код')


@pytest.mark.parametrize('question', [
    'Станет ли МТС продавцом электричества после подключения?',
    'Назови точную итоговую цену модернизации, данных по объекту нет.',
    'Гарантируете окупаемость нашего проекта за 18 месяцев?',
])
def test_project_promises_retrieve_general_limits(question):
    """Ограничения конкретных обещаний находятся раньше индикативных цифр."""
    chunks = make_chunks('scope.md', [('# Область применения продуктов и ограничения\n\n'
        '## Общие границы\n\nМТС не поставляет электроэнергию. Стоимость и эффект '
        'определяются после изучения данных объекта.', None, None)], 1800)
    hits = SearchIndex(tuple(chunks)).search_request(CoreRequest(message=question), 1)
    assert hits and hits[0].chunk.section == 'Общие границы'
    assert hits[0].required


def test_general_limits_do_not_override_normal_category_price_question():
    """Обычный вопрос о тарифной категории не требует правил точной цены проекта."""
    chunks = make_chunks('scope.md', [('# Область применения продуктов и ограничения\n\n'
        '## Общие границы\n\nЦена проекта определяется индивидуально.', None, None)], 1800)
    index = SearchIndex(tuple(chunks))
    request = CoreRequest(message='Как зависит цена электроэнергии от ценовой категории?')
    assert index.fact_anchors(request, search_intent(request)) == ()


@pytest.mark.parametrize("budget", [620, 2000])
def test_second_required_table_is_completed_before_generic_guards(budget):
    """Оговорки первого документа не вытесняют строки обязательной второй таблицы."""
    from core.retrieval import Hit
    first = make_chunks('first.md', [('# Первое решение\n\n## Обзор\n\nКраткий обзор.\n\n'
        '## Ограничения\n\n' + 'Общее ограничение. ' * 30, None, None)], 1800)
    second = make_chunks('second.md', [('# Второе решение\n\n## Точность\n\n'
        'Вода: более 90 процентов.\n\nЭлектроэнергия: более 95 процентов.', None, None)], 1800)
    index = SearchIndex(tuple(first + second))
    overview = next(c for c in first if c.section == 'Обзор')
    table = [c for c in second if c.section == 'Точность']
    assert len(table) == 2
    context = index.context((Hit(overview, 1), Hit(table[0], 0, True)), budget)
    assert all(c in context for c in table)
    assert sum(len(c.text) for c in context) <= budget
    assert len({c.chunk_id for c in context}) == len(context)
    if budget == 2000:
        assert any(c.section == 'Ограничения' for c in context)


def test_excluded_guards_do_not_spend_engine_context_budget(tmp_path):
    """Исключённый продукт не вытесняет условия выбранного продукта до фильтрации."""
    chunks = make_chunks('shared.md', [('# Общая инструкция\n\n## Экспорт\n\n'
        'Выгрузка CSV доступна.\n\n## Ограничения солнечные электростанции\n\n'
        + 'Панели требуют осмотра. ' * 80
        + '\n\n## Ограничения платформы\n\n'
        + 'Экспорт требует согласования оператора. ' * 8, None, None)], 3000)

    class Client:
        def complete(self, messages, deadline):
            documents = json.loads(messages[-1]['content'])['documents']
            texts = ' '.join(doc['text'] for doc in documents)
            assert 'согласования оператора' in texts
            assert 'Панели требуют' not in texts
            return {'status': 'answered', 'answer': 'Выгрузка требует согласования.', 'evidence': ['D1']}

    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder',
                   top_k=1, context_chars=2000, review_answers=False)
    result = EnergytoolCore(cfg, chunks=chunks, client=Client()).respond(
        CoreRequest(message='Панели не нужны, нужен учёт. Есть выгрузка CSV?'))
    assert result.core_status == 'answered'


def test_cached_product_labels_do_not_keep_previous_client_exclusions():
    """Общий индекс хранит метки базы, исключения остаются в текущем запросе."""
    chunks = make_chunks('solar.md', [('# Солнечные электростанции\n\n'
        '## Ограничения\n\nСетевая станция зависит от внешней сети.', None, None)], 1800)
    index = SearchIndex(tuple(chunks))
    excluded = search_intent(CoreRequest(message='Панели не нужны, нужен учёт.'))
    included = search_intent(CoreRequest(message='Нужна солнечная станция.'))
    assert not index.allowed(chunks[0], excluded)
    assert index.allowed(chunks[0], included)
    assert not index.allowed(chunks[0], excluded)


@pytest.mark.parametrize('message,active,excluded', [
    ('Панели не нужны и нужен учёт.', 'облачная платформа', 'солнечные электростанции'),
    ('Нужен учёт и не нужны панели.', 'облачная платформа', 'солнечные электростанции'),
    ('Отказываемся от панелей и не отказываемся от учёта по арендаторам.',
     'облачная платформа', 'солнечные электростанции'),
])
def test_independent_product_decisions_joined_by_and(message, active, excluded):
    """Союз с новым сказуемым отделяет решение об одном продукте от другого."""
    intent = search_intent(CoreRequest(message=message))
    assert active in intent.active
    assert excluded in intent.excluded
    assert active not in intent.excluded


def test_shared_product_list_keeps_common_refusal():
    """Список без нового сказуемого сохраняет общий отказ от обоих продуктов."""
    intent = search_intent(CoreRequest(message='Не нужны панели и выход на оптовый рынок.'))
    assert not intent.active
    assert {'солнечные электростанции', 'выход на оптовый рынок'} <= set(intent.excluded)


@pytest.mark.parametrize('question', [
    'Солнечная станция нам не нужна?',
    'Нам не нужны панели?',
    'Отказываемся от солнечной станции?',
])
def test_negative_product_question_is_not_client_refusal(question, index):
    """Вопрос о необходимости продукта сохраняет источники для ответа."""
    intent = search_intent(CoreRequest(message=question))
    assert 'солнечные электростанции' not in intent.excluded
    assert any('Солнечные' in h.chunk.title for h in index.search_request(CoreRequest(message=question)))
