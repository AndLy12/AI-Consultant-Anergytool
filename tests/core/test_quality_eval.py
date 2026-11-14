"""Регрессии для локального сравнения, формата источников и совместимости прокси."""

import json
import time

import httpx
import pytest

from core.config import Settings
from core.documents import make_chunks
from core.llm import QwenClient
from core.validation import assemble
from core_contract import CoreRequest, ModelResponseError
from scripts.judge_answers import metrics


def test_compact_sources_keep_original_coordinates():
    chunks = make_chunks('example.md', [('Учебный тариф с двумя зонами.', None, None)], 1800)
    request = CoreRequest(message='Сколько зон?')
    raw = {'status': 'answered', 'answer': 'Две зоны.', 'evidence': ['D1']}
    result = assemble(raw, chunks, request)
    assert result.sources[0].chunk_id == chunks[0].chunk_id
    assert result.sources[0].excerpt == chunks[0].text
    with pytest.raises(ModelResponseError):
        assemble(dict(raw, evidence=['D2']), chunks, request)


def test_standard_proxy_rejecting_extension_can_generate(tmp_path):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if 'reasoning_effort' in body:
            return httpx.Response(422, json={'error': {'param': 'reasoning_effort'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})

    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder', retries=0)
    client = QwenClient(cfg, httpx.MockTransport(handler))
    for _ in range(2):
        assert client.complete([], time.monotonic() + 2) == {}
    assert len(requests) == 3
    assert requests[-1]['chat_template_kwargs'] == {'enable_thinking': False}
    assert 'reasoning_effort' not in requests[-1]


def test_judge_metrics_ties_and_exclusions():
    assert metrics([]) is None
    assert metrics([.5, .5])['score'] == .5
    assert metrics([1, .75, .5, 0])['win_rate'] == pytest.approx(2/3)


@pytest.mark.parametrize('review_fails', [False, True])
@pytest.mark.parametrize('wrapped', [False, True])
def test_review_corrects_answer_or_preserves_valid_draft(tmp_path, review_fails, wrapped):
    """Проверка уточняет ответ, а её сетевой сбой сохраняет готовую консультацию."""
    from core.engine import EnergytoolCore
    from core_contract import CoreUnavailable

    chunks = make_chunks('example.md', [('Панели допускаются на вертикальной стене.', None, None)], 1800)
    calls = []
    class Client:
        def complete(self, messages, deadline):
            data = json.loads(messages[-1]['content'])
            calls.append((data, deadline))
            if 'draft' in data and review_fails:
                raise CoreUnavailable('Сбой дополнительной проверки')
            answer = {'status': 'answered', 'answer': 'На стене допускаются.' if 'draft' in data else 'Монтаж возможен.',
                      'evidence': ['D1']}
            return {'checks': {}, 'result': answer} if 'draft' in data and wrapped else answer
        def close(self):
            pass

    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder')
    core = EnergytoolCore(cfg, chunks=chunks, client=Client())
    result = core.respond(CoreRequest(message='Можно разместить панели на стене?'))
    assert len(calls) == 2
    assert calls[1][0]['documents'] == calls[0][0]['documents']
    assert calls[1][1] < calls[0][1]
    assert result.answer == ('Монтаж возможен.' if review_fails else 'На стене допускаются.')
    assert bool(result.warnings) == review_fails


def test_structured_summary_uses_only_original_user_messages():
    """Сбой формы черновика не теряет ответ и не придумывает контакты."""
    chunks = make_chunks('example.md', [('В учебном стенде есть экспорт.', None, None)], 1800)
    request = CoreRequest(message='Подготовь резюме для заявки.', history=[
        {'role': 'user', 'content': 'Компания Учебный завод. Нужно видеть расход по цехам.'}])
    raw = {'status': 'answered', 'answer': 'Учебный стенд поддерживает экспорт.',
           'evidence': ['D1'], 'summary_draft': {'phone': '+70000000000'}}
    result = assemble(raw, chunks, request)
    assert result.summary_draft == request.history[0].content
    assert '+70000000000' not in result.model_dump_json()

@pytest.mark.parametrize('message', [
    'Назовите итоговую стоимость станции.',
    'Что нужно для заявки?',
    'Заявку не надо. Просто объясните условия.',
    'Не оформляй заявку, расскажи про условия.',
])
def test_consultation_does_not_create_unsolicited_summary(message):
    """Справочный вопрос и отказ от заявки не создают черновик автоматически."""
    result = assemble({'status': 'insufficient_data', 'summary_draft': message}, (),
                      CoreRequest(message=message))
    assert result.summary_draft is None


def test_proxy_rejecting_both_extensions_stops_negotiation(tmp_path):
    """Отключение двух дополнительных полей занимает не более двух повторов."""
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if 'reasoning_effort' in body:
            return httpx.Response(400, text='Unsupported reasoning_effort')
        if 'chat_template_kwargs' in body:
            return httpx.Response(400, text='Unsupported chat_template_kwargs')
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder', retries=0)
    client = QwenClient(cfg, httpx.MockTransport(handler))
    assert client.complete([], time.monotonic() + 2) == {}
    assert client.complete([], time.monotonic() + 2) == {}
    assert len(calls) == 4


def test_proxy_without_any_optional_fields_uses_standard_request(tmp_path):
    """Прокси со стандартным чатом получает запрос после отключения трёх расширений."""
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if any(key in body for key in ('reasoning_effort', 'chat_template_kwargs', 'response_format')):
            return httpx.Response(422)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder', retries=0)
    client = QwenClient(cfg, httpx.MockTransport(handler))
    assert client.complete([], time.monotonic() + 2) == {}
    assert client.complete([], time.monotonic() + 2) == {}
    assert len(calls) == 6


def test_proxy_rejecting_json_mode_preserves_thinking_settings(tmp_path):
    """Отказ в JSON-режиме не включает длинные рассуждения модели."""
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if 'response_format' in body:
            return httpx.Response(400)
        assert body['reasoning_effort'] == 'none'
        assert body['chat_template_kwargs'] == {'enable_thinking': False}
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder', retries=0)
    client = QwenClient(cfg, httpx.MockTransport(handler))
    assert client.complete([], time.monotonic() + 2) == {}
    assert len(calls) == 3


def test_object_only_proxy_keeps_concurrent_mode_negotiation(tmp_path):
    """Режим json_object сохраняется после запоздалого отказа другого потока."""
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body['response_format']['type'] == 'json_schema':
            # Имитируем, что другой запрос уже выбрал режим json_object.
            client.json_format = 'json_object'
            return httpx.Response(400, text='Unsupported response_format type json_schema')
        assert body['response_format']['type'] == 'json_object'
        assert body['reasoning_effort'] == 'none'
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder', retries=0)
    client = QwenClient(cfg, httpx.MockTransport(handler))
    assert client.complete([], time.monotonic() + 2) == {}
    assert client.complete([], time.monotonic() + 2) == {}
    assert len(calls) == 3
    assert client.use_json_mode


def test_reviewer_schema_requires_result(tmp_path):
    """Провайдер получает переиспользуемый контракт с обязательным результатом."""
    def handler(request):
        schema = json.loads(request.content)['response_format']['json_schema']['schema']
        assert schema['required'] == ['issues', 'follow_up_question', 'result']
        assert 'ModelSelection' in schema['$defs']
        return httpx.Response(200, json={'choices': [{'message': {'content':
            '{"issues":[],"result":{"status":"insufficient_data"}}'}}]})
    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder', retries=0)
    result = QwenClient(cfg, httpx.MockTransport(handler)).review([], time.monotonic() + 2)
    assert result['result']['status'] == 'insufficient_data'


@pytest.mark.parametrize('message,answer,expected', [
    ('Какие условия?', 'Данные доступны (D1).', 'Данные доступны.'),
    ('Какие условия?', 'Данные доступны [D1].', 'Данные доступны.'),
    ('У нас модель D1.', 'Проверьте модель (D1).', 'Проверьте модель (D1).'),
    ('У нас модель D10.', 'Данные доступны (D1).', 'Данные доступны.'),
    ('Какие условия?', 'Проверьте модель (D99).', 'Проверьте модель (D99).'),
])
def test_internal_aliases_do_not_leak_into_answer(message, answer, expected):
    """Служебная метка убирается, код прибора клиента сохраняется."""
    chunks = make_chunks('example.md', [('Учебные данные доступны.', None, None)], 1800)
    result = assemble({'status': 'answered', 'answer': answer, 'evidence': ['D1']},
                      chunks, CoreRequest(message=message))
    assert result.answer == expected


def test_photo_intent_includes_different_prepositions():
    """Запрос «на фото» выбирает тот же продукт, что и «по фотографии»."""
    from core.retrieval import SearchIndex
    chunks = make_chunks('photo.md', [('# Распознавание показаний прибора\n\nГод производства прибора.',
                                      'Распознавание показаний', None)], 1800)
    chunks += make_chunks('meter.md', [('# Документы на прибор\n\nГод поверки в документах.',
                                       'Документы на прибор', None)], 1800)
    index = SearchIndex(tuple(chunks))
    result = index.search_request(CoreRequest(message='На фото нужен год поверки. Какой год поддерживается?'), 1)
    assert 'Распознавание' in result[0].chunk.title


@pytest.mark.parametrize('winner,strength,score', [
    ('A', 'large', 1), ('A', 'slight', .75), ('tie', 'equal', .5),
    ('B', 'slight', .25), ('B', 'large', 0),
])
def test_preference_maps_to_correct_side(winner, strength, score):
    """Обратный порядок ответов не меняет смысл оценки победителя."""
    from scripts.judge_answers import PairVerdict
    assert PairVerdict(winner=winner, strength=strength, reason='Проверка стороны').score_a == score


def test_answer_keeps_a_separate_follow_up_question():
    """Связный ответ и следующий вопрос совместимы со статусом answered."""
    chunks = make_chunks('example.md', [('Учебные данные доступны.', None, None)], 1800)
    result = assemble({'status': 'answered', 'answer': 'Данные доступны.', 'evidence': ['D1'],
                       'question': 'Какие приборы установлены?'}, chunks, CoreRequest(message='Можно подключить?'))
    assert result.answer == 'Данные доступны.\n\nКакие приборы установлены?'


@pytest.mark.parametrize('text,expected', [
    ('Кажется, четвёртая.', 'четверт'), ('4-я', 'четверт'), ('4', 'четверт'),
    ('Не третья, а шестая.', 'шест'), ('Потребление 4 кВт', None),
])
def test_explicit_price_category(text, expected):
    """Число мощности не становится номером категории, исправление имеет приоритет."""
    from core.retrieval import price_category
    assert price_category(text) == expected


@pytest.mark.parametrize('message', ['С чего нам начать?', 'Часть показаний приходит на фото. С чего нам начать?'])
def test_planning_keeps_linked_metering_products(message):
    """Фото в смешанном сборе не вытесняет исходную задачу учёта по арендаторам."""
    from core.retrieval import SearchIndex
    chunks = make_chunks('platform.md', [('# Облачная платформа\n\nУчёт по арендаторам.',
                                        'Облачная платформа', None)], 1800)
    chunks += make_chunks('photo.md', [('# Распознавание показаний\n\nФото прибора.',
                                      'Распознавание показаний', None)], 1800)
    chunks += make_chunks('solar.md', [('# Солнечные электростанции\n\nМонтаж панелей.',
                                      'Солнечные электростанции', None)], 1800)
    index = SearchIndex(tuple(chunks))
    history = [{'role': 'user', 'content': 'Нужен учёт по арендаторам.'},
               {'role': 'user', 'content': 'Некоторые присылают фото.'}]
    request = CoreRequest(message=message, history=history)
    hits = index.search_request(request, 2)
    assert {h.chunk.title for h in hits} == {'Облачная платформа', 'Распознавание показаний'}
    # Последующая смена продукта сохраняет приоритет перед старой смешанной задачей.
    history += [{'role': 'user', 'content': 'Теперь обсудим солнечные панели.'}]
    hits = index.search_request(CoreRequest(message='Что понадобится для начала?', history=history), 1)
    assert hits[0].chunk.title == 'Солнечные электростанции'


@pytest.mark.parametrize('message', [
    'Для учёта по арендаторам какие данные понадобятся?',
    'Для учёта по арендаторам что вам нужно узнать, чтобы подобрать решение?',
])
def test_product_checklist_precedes_general_conversation(message):
    """Для подключения известного продукта сначала нужны данные именно этого продукта."""
    from core.retrieval import SearchIndex
    chunks = make_chunks('platform.md', [('# Облачная платформа\n\nУчёт по арендаторам.', None, None)], 1800)
    chunks += make_chunks('prep.md', [('# Подготовка\n\n## Для платформы учёта\n\nКоличество и тип приборов.', None, None)], 1800)
    chunks += make_chunks('general.md', [('# Общие вопросы\n\nС чего начать разговор и выбор продукта.', None, None)], 1800)
    index = SearchIndex(tuple(chunks))
    hits = index.search_request(CoreRequest(message=message), 5)
    assert hits[0].chunk.section == 'Для платформы учёта'
    assert hits[0].required
    assert all(h.chunk.title != 'Общие вопросы' for h in hits)


def test_requested_summary_is_visible_in_csv_answer():
    """CSV не передаёт отдельное поле черновика, поэтому явная просьба видна в тексте."""
    chunks = make_chunks('example.md', [('Учёт доступен.', None, None)], 1800)
    request = CoreRequest(message='Подготовь резюме для заявки.', history=[
        {'role': 'user', 'content': 'У нас два склада и десять приборов.'}])
    result = assemble({'status': 'answered', 'answer': 'Учёт доступен.', 'evidence': ['D1'],
                       'summary_draft': 'два склада и десять приборов'}, chunks, request)
    assert result.summary_draft in result.answer
    assert 'Черновик по вашим сообщениям' in result.answer


@pytest.mark.parametrize('heading', ['Резюме:', 'Готовлю резюме по вашим словам:'])
def test_visible_summary_does_not_append_outdated_history(heading):
    """Готовое резюме с исправлением не дублируется хронологическим черновиком."""
    chunks = make_chunks('example.md', [('Учёт доступен.', None, None)], 1800)
    request = CoreRequest(message='Подготовь резюме для заявки.', history=[
        {'role': 'user', 'content': 'У нас шесть приборов.'},
        {'role': 'user', 'content': 'Исправляю: десять приборов.'}])
    answer = heading + ' планируется подключение десяти приборов к учёту.'
    result = assemble({'status': 'answered', 'answer': answer, 'evidence': ['D1']}, chunks, request)
    assert result.answer == answer


@pytest.mark.parametrize('phrase,question', [
    ('какие приборы стоят на объекте', 'Какие приборы стоят на объекте?'),
    ('какая модель прибора установлена', 'Какая модель прибора установлена?'),
    ('тип приборов и их возраст', 'Какие приборы стоят и каков их возраст?'),
])
def test_embedded_parameter_request_is_not_duplicated(phrase, question):
    """Просьба с косвенным вопросом уже является следующим шагом консультации."""
    chunks = make_chunks('example.md', [('Учёт доступен.', None, None)], 1800)
    answer = 'Учёт доступен. Для следующего шага уточните, ' + phrase + '.'
    result = assemble({'status': 'answered', 'answer': answer, 'evidence': ['D1'],
                       'question': question}, chunks, CoreRequest(message='Можно подключить?'))
    assert result.answer == answer


def test_evening_solar_question_keeps_profile_condition():
    """Оговорка о несовпадении выработки с нагрузкой важнее общей справки о станции."""
    from core.retrieval import SearchIndex
    chunks = make_chunks('solar.md', [('# Солнечные электростанции\n\n## Сетевая станция\n\nСетевая станция снижает платёж.\n\n## Не подходит, если\n\nНочная нагрузка: выработка не совпадает с потреблением.', None, None)], 1800)
    hits = SearchIndex(tuple(chunks)).search_request(CoreRequest(message='Есть смысл при вечерней нагрузке?', history=[
        {'role': 'user', 'content': 'Нужна солнечная станция.'}]), 1)
    assert hits[0].required and 'не совпадает' in hits[0].chunk.text


def test_profile_context_does_not_mix_in_equipment_power_range():
    """Временной профиль клиента не превращается в диапазон мощности станции."""
    from core.retrieval import SearchIndex
    chunks = make_chunks('solar.md', [('# Солнечные электростанции\n\n## Не подходит, если\n\nНочная нагрузка: выработка не совпадает с потреблением.\n\n## Целевой диапазон мощности\n\nТиповая станция от 15 до 500 кВт.\n\n## Если пропадёт электричество, станция обеспечит объект\n\nДля резерва нужна гибридная конфигурация.', None, None)], 1800)
    index = SearchIndex(tuple(chunks))
    hits = index.search_request(CoreRequest(message='Солнечная станция при вечерней нагрузке имеет смысл?'), 5)
    context = index.context(hits, 14000)
    assert any('гибридная' in c.text for c in context)
    assert all('500' not in c.text for c in context)
    # Второй явный вопрос о типовом диапазоне не должен потерять свой источник.
    mixed = index.context(index.search_request(CoreRequest(
        message='Солнечная станция 800 кВт типовая? Имеет смысл при вечерней нагрузке?'), 5), 14000)
    assert any('500' in c.text for c in mixed)


def test_reranker_does_not_remove_product_checklist(tmp_path):
    """Противоположный порядок реранкера не вытесняет список подготовки выбранного продукта."""
    from core.engine import EnergytoolCore
    chunks = make_chunks('platform.md', [('# Облачная платформа\n\nУчёт по арендаторам.', None, None)], 1800)
    chunks += make_chunks('prep.md', [('# Подготовка\n\n## Для платформы учёта\n\nКоличество и тип приборов.', None, None)], 1800)
    class Client:
        def rerank(self, query, documents, count, deadline):
            return list(reversed(range(len(documents))))[:count]
        def complete(self, messages, deadline):
            doc = json.loads(messages[-1]['content'])['documents'][0]
            assert doc['text'] == 'Количество и тип приборов.'
            return {'status': 'answered', 'answer': doc['text'], 'evidence': ['D1']}
    settings = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder',
                        top_k=1, reranker_model='rerank', review_answers=False)
    core = EnergytoolCore(settings, chunks=tuple(chunks), client=Client())
    assert core.respond(CoreRequest(message='Для учёта по арендаторам какие данные понадобятся?')).sources


def test_return_to_first_question_restores_first_product():
    """Ссылка на первый вопрос восстанавливает тему после промежуточного переключения."""
    from core.retrieval import SearchIndex
    chunks = make_chunks('solar.md', [('# Солнечные электростанции\n\nГибридная конфигурация обеспечивает резерв.', None, None)], 1800)
    chunks += make_chunks('photo.md', [('# Распознавание показаний\n\nФото передаёт показания.', None, None)], 1800)
    request = CoreRequest(message='Вернёмся к первому вопросу: что нужно для резерва при отключении?', history=[
        {'role': 'user', 'content': 'Здравствуйте.'},
        {'role': 'user', 'content': 'Солнечная станция обеспечит резерв?'},
        {'role': 'user', 'content': 'Теперь расскажи про распознавание по фото.'}])
    assert SearchIndex(tuple(chunks)).search_request(request, 1)[0].chunk.title == 'Солнечные электростанции'


def test_context_overflow_does_not_disable_provider_capabilities(tmp_path):
    """После длинного запроса короткий сохраняет JSON-схему и отключение мышления."""
    from core_contract import ProviderError
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(400, json={'error': {'type': 'exceed_context_size_error',
                'message': 'request exceeds the available context size'}})
        assert body['response_format']['type'] == 'json_schema'
        assert body['reasoning_effort'] == 'none'
        assert body['chat_template_kwargs']['enable_thinking'] is False
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    client = QwenClient(Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder'),
                        httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as error:
        client.complete([], time.monotonic() + 2)
    assert error.value.category == 'context_length'
    assert client.complete([], time.monotonic() + 2) == {}
    assert len(calls) == 2


@pytest.mark.parametrize('answer,evidence,expected', [
    ('Итог после бесплатного обследования.', ['Бесплатный расчёт.'], 'Итог после обследования.'),
    ('Нужно бесплатное выездное обследование.', ['Бесплатное выездное обследование.'], 'Нужно бесплатное выездное обследование.'),
    ('Мы не обещаем бесплатное обследование.', [], 'Мы не обещаем бесплатное обследование.'),
    ('Бесплатное обследование не подтверждено.', [], 'Бесплатное обследование не подтверждено.'),
    ('Бесплатный расчёт после обследования.', [], 'Бесплатный расчёт после обследования.'),
])
def test_free_calculation_does_not_imply_free_physical_work(answer, evidence, expected):
    """Различаем виды работ, подтверждённую бесплатность и отказ от обещания."""
    from core.validation import grounded_free_work
    assert grounded_free_work(answer, evidence) == expected


def test_unconfirmed_free_work_is_removed_during_response_assembly():
    """Консультация сохраняется, неподтверждённое прилагательное убирается с диагностикой."""
    chunks = make_chunks('example.md', [('Предварительный расчёт бесплатен.', None, None)], 1800)
    result = assemble({'status': 'answered', 'answer': 'Итоговая цена после бесплатного обследования.',
                       'evidence': ['D1']}, chunks, CoreRequest(message='Как получить итоговую цену?'))
    assert result.answer == 'Итоговая цена после обследования.'
    assert result.warnings


@pytest.mark.parametrize('existing', [False, True])
def test_reading_answer_keeps_verified_commercial_use_condition(existing):
    """Пропущенное условие добавляется из контекста с источником, существующее не повторяется."""
    condition = 'Распознанное показание является дополнительным подтверждением. В коммерческий учёт оно попадает только после верификации.'
    chunks = make_chunks('photo.md', [('# Распознавание показаний\n\nПоказания считываются раздельно.\n\n' + condition, None, None)], 1800)
    answer = 'Да, показания считываются раздельно.' + (' После верификации.' if existing else '')
    result = assemble({'status': 'answered', 'answer': answer, 'evidence': ['D1'],
                       'question': 'Сколько приборов нужно подключить?'}, chunks, CoreRequest(message='Оба показания?'))
    assert result.answer.lower().count('верификац') == 1
    assert result.answer.endswith('?')
    if not existing:
        assert any(s.excerpt == condition for s in result.sources)


def test_reading_condition_is_not_borrowed_from_other_product():
    """Совпадение слов в другом документе не подтверждает ограничение выбранного продукта."""
    chunks = make_chunks('photo.md', [('# Распознавание показаний\n\nПоказания считываются.', None, None)], 1800)
    chunks += make_chunks('other.md', [('Распознанное показание является дополнительным подтверждением после верификации.', None, None)], 1800)
    result = assemble({'status': 'answered', 'answer': 'Да, показания считываются.', 'evidence': ['D1']},
                      chunks, CoreRequest(message='Как работает сервис?'))
    assert 'верификац' not in result.answer
