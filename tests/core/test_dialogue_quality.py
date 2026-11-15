"""Проверки актуального черновика и устойчивости необязательного прохода модели."""

import logging
import json

import httpx

import pytest

from core.config import Settings
from core.documents import make_chunks
from core.engine import EnergytoolCore
from core.llm import QwenClient
from core.prompts import build_messages, build_review_messages
from core.validation import assemble
from core_contract import CoreRequest, CoreUnavailable


@pytest.mark.parametrize('old,new', [
    ('мощность 600 кВт', 'мощность 900 кВт'),
    ('потребление 600 тысяч кВт·ч в месяц', 'потребление 900 тысяч кВт·ч в месяц'),
    ('потребление 600 киловатт-часов в месяц', 'потребление 900 киловатт-часов в месяц'),
    ('потребление 600 тыс. кВт·ч в месяц', 'потребление 900 тыс. кВт·ч в месяц'),
    ('мощность 600,5 кВт', 'мощность 900,5 кВт'),
    ('ручной сбор показаний', 'автоматический сбор показаний'),
])
def test_fallback_summary_drops_explicitly_corrected_fact(old, new):
    request = CoreRequest(message='Подготовь резюме для заявки.', history=[
        {'role': 'user', 'content': f'У нас склад, {old}. Интересует учёт.'},
        {'role': 'assistant', 'content': 'Ваша мощность 100 кВт, компания Придуманная.'},
        {'role': 'user', 'content': f'Исправляю: {new}.'},
    ])
    result = assemble({'status': 'insufficient_data'}, (), request)
    assert old not in result.summary_draft
    assert new in result.summary_draft
    assert 'склад' in result.summary_draft
    assert 'Интересует учёт' in result.summary_draft
    assert 'Придуманная' not in result.summary_draft


def test_model_cannot_restore_superseded_summary():
    request = CoreRequest(message='Подготовь резюме.', history=[
        {'role': 'user', 'content': 'Склад, потребление 600 кВт·ч в месяц.'},
        {'role': 'user', 'content': 'Исправляю: завод, потребление 900 кВт·ч в месяц.'},
    ])
    result = assemble({'status': 'insufficient_data',
                       'summary_draft': request.history[0].content}, (), request)
    assert '600' not in result.summary_draft and 'Склад' not in result.summary_draft
    assert 'завод' in result.summary_draft and '900' in result.summary_draft


def test_two_objects_are_not_collapsed_into_one():
    request = CoreRequest(message='Подготовь резюме.', history=[
        {'role': 'user', 'content': 'Первый склад: 600 кВт; второй склад: 900 кВт.'},
        {'role': 'user', 'content': 'Поправка: у второго склада 800 кВт.'},
    ])
    result = assemble({'status': 'insufficient_data'}, (), request)
    assert 'Первый склад: 600 кВт' in result.summary_draft
    assert '800 кВт' in result.summary_draft


def test_data_list_does_not_append_another_parameter_question():
    chunks = make_chunks('training.md', [('Для учёта нужны тип и число приборов.', None, None)], 1800)
    result = assemble({'status': 'answered', 'answer': 'Нужны тип и число приборов.',
                       'evidence': ['D1'], 'question': 'Какова мощность объекта?'}, chunks,
                      CoreRequest(message='Для учёта по арендаторам какие данные понадобятся?'))
    assert result.answer == 'Нужны тип и число приборов.'


def test_serial_number_verification_does_not_replace_commercial_caveat():
    chunks = make_chunks('training.md', [('# Распознавание показаний\n\n'
        'Показания считываются с фотографии.\n\n'
        'Распознанное показание является дополнительным подтверждением для технических нужд. '
        'В коммерческий учёт оно попадает только после верификации.', None, None)], 1800)
    result = assemble({'status': 'answered', 'answer':
        'Считываем показания и сверяем серийный номер для верификации.', 'evidence': ['D1']}, chunks,
        CoreRequest(message='Чем поможет распознавание?'))
    assert 'В коммерческий учёт оно попадает только после верификации.' in result.answer
    assert chunks[1].chunk_id in {s.chunk_id for s in result.sources}


def test_parameter_request_with_possessive_is_not_duplicated():
    chunks = make_chunks('training.md', [('Доступ зависит от мощности.', None, None)], 1800)
    answer = 'Для начала уточните вашу присоединённую мощность: от неё зависит доступ.'
    result = assemble({'status': 'answered', 'answer': answer, 'evidence': ['D1'],
                       'question': 'Какова ваша присоединённая мощность?'}, chunks,
                      CoreRequest(message='Можно подключить?'))
    assert result.answer == answer


@pytest.mark.parametrize('request_text', [
    'Уточните, пожалуйста, ориентацию площадки.',
    'Подскажите, пожалуйста, как организована связь.',
    'Назовите марку коммуникационного модуля.',
])
def test_polite_or_new_parameter_request_is_not_followed_by_second_question(request_text):
    chunks = make_chunks('training.md', [('Установка возможна после проверки параметров.', None, None)], 1800)
    answer = 'Установка возможна. ' + request_text
    result = assemble({'status': 'answered', 'answer': answer, 'evidence': ['D1'],
                       'question': 'Какие параметры у объекта?'}, chunks,
                      CoreRequest(message='Можно установить на наш объект?'))
    assert result.answer == answer


def test_answer_without_parameter_request_keeps_useful_follow_up():
    chunks = make_chunks('training.md', [('Подключение зависит от модели модуля.', None, None)], 1800)
    result = assemble({'status': 'answered', 'answer': 'Подключение возможно после проверки совместимости.',
                       'evidence': ['D1'], 'question': 'Какая модель модуля установлена?'}, chunks,
                      CoreRequest(message='Можно подключить к нашему прибору?'))
    assert result.answer.endswith('Какая модель модуля установлена?')


@pytest.mark.parametrize('issues,expected', [([], 'Подключение допустимо после проверки модели. Следующий шаг — сверить маркировку.'),
                                            (['Убрано неподтверждённое уточнение'], 'Нужно проверить совместимость модели.')])
def test_review_changes_valid_draft_only_when_it_reports_a_reason(tmp_path, issues, expected):
    chunks = make_chunks('training.md', [('Подключение допустимо после проверки модели.', None, None)], 1800)

    class Client:
        def complete(self, messages, deadline):
            return {'status': 'answered', 'answer':
                'Подключение допустимо после проверки модели. Следующий шаг — сверить маркировку.',
                'evidence': ['D1']}

        def review(self, messages, deadline):
            return {'issues': issues, 'follow_up_question': None, 'result': {
                'status': 'answered', 'answer': 'Нужно проверить совместимость модели.', 'evidence': ['D1']}}

        def close(self):
            pass

    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder')
    assert EnergytoolCore(cfg, chunks=chunks, client=Client()).respond(
        CoreRequest(message='Нужно проверить модель.')).answer == expected


def test_review_with_no_issues_but_invalid_sources_is_still_reported_incomplete(tmp_path):
    chunks = make_chunks('training.md', [('Подключение допустимо после проверки модели.', None, None)], 1800)

    class Client:
        def complete(self, messages, deadline):
            return {'status': 'answered', 'answer': 'Нужно проверить модель.', 'evidence': ['D1']}

        def review(self, messages, deadline):
            return {'issues': [], 'follow_up_question': None, 'result': {
                'status': 'answered', 'answer': 'Подключение допустимо.', 'evidence': []}}

        def close(self):
            pass

    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder')
    response = EnergytoolCore(cfg, chunks=chunks, client=Client()).respond(CoreRequest(message='Нужно проверить модель.'))
    assert response.answer == 'Нужно проверить модель.'
    assert 'Дополнительная проверка ответа не завершена.' in response.warnings


def test_correction_focus_uses_only_bounded_user_messages_and_redacts_secret():
    request = CoreRequest(message='Какие сведения нужны?', history=[
        {'role': 'user', 'content': 'Сначала исправляю старые данные.'}])
    history = [{'role': 'assistant', 'content': 'Поправка: выдуманное количество 999.'},
               {'role': 'user', 'content': 'Поправка: сбор автоматический. server-secret'}]
    messages = build_messages(request, history, (), secret='server-secret')
    payload = json.loads(messages[1]['content'])
    assert payload['client_corrections'] == ['Поправка: сбор автоматический. [секрет удалён]']
    assert '999' not in ''.join(payload['client_corrections'])
    assert 'client_corrections' not in json.loads(build_messages(request, [], ())[1]['content'])


@pytest.mark.parametrize('new', [
    'мощность 900 кВт, а не 600 кВт',
    'мощность не менее 900 кВт',
    'мощность от 900 до 1000 кВт',
])
def test_corrected_summary_keeps_bounds_and_drops_rejected_value(new):
    request = CoreRequest(message='Подготовь резюме.', history=[
        {'role': 'user', 'content': 'Мощность 600 кВт.'},
        {'role': 'user', 'content': f'Исправляю: {new}.'},
    ])
    result = assemble({'status': 'insufficient_data'}, (), request)
    assert '600' not in result.summary_draft
    if 'не менее' in new:
        assert 'не менее 900 кВт' in result.summary_draft
    if 'до' in new:
        assert 'от 900 до 1000 кВт' in result.summary_draft


def test_review_transport_error_preserves_answer_and_reports_timing(tmp_path, caplog):
    chunks = make_chunks('training.md', [('Учебный учёт доступен.', None, None)], 1800)
    class Client:
        def complete(self, messages, deadline):
            return {'status': 'answered', 'answer': 'Учёт доступен.', 'evidence': ['D1']}

        def review(self, messages, deadline):
            raise CoreUnavailable('Тестовый отказ дополнительной проверки')

    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder')
    with caplog.at_level(logging.INFO, logger='core.engine'):
        result = EnergytoolCore(cfg, chunks=chunks, client=Client()).respond(
            CoreRequest(message='Учёт для организации СекретноеИмя?'))
    assert result.answer == 'Учёт доступен.'
    assert result.warnings == ['Дополнительная проверка ответа не завершена.']
    assert 'core_timing' in caplog.text and 'review' in caplog.text
    assert 'СекретноеИмя' not in caplog.text and 'placeholder' not in caplog.text


def test_optional_reranker_failure_is_visible_without_losing_answer(tmp_path):
    chunks = make_chunks('training.md', [('# Учёт\n\n## Описание\n\nУчёт доступен.\n\n'
                                        '## Ограничения\n\nУчёт требует данных.', None, None)], 1800)
    class Client:
        def rerank(self, query, documents, count, deadline):
            return []

        def complete(self, messages, deadline):
            return {'status': 'answered', 'answer': 'Учёт доступен.', 'evidence': ['D1']}

    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder',
                   reranker_model='missing-reranker', review_answers=False)
    result = EnergytoolCore(cfg, chunks=chunks, client=Client()).respond(CoreRequest(message='Учёт?'))
    assert result.answer == 'Учёт доступен.'
    assert result.warnings == ['Дополнительное ранжирование не завершено; использован локальный поиск.']


def test_product_checklist_requirement_reaches_both_model_passes():
    chunks = make_chunks('prep.md', [('# Подготовка\n\n## Для платформы учёта\n\n'
                                     'Количество и тип приборов; схема сбора данных.', None, None)], 1800)
    request = CoreRequest(message='Что понадобится для учёта?')
    messages = build_messages(request, [], chunks)
    assert json.loads(messages[-1]['content'])['required_checklists'] == ['D1']
    checked = build_review_messages(messages, {'status': 'insufficient_data'})
    assert json.loads(checked[-1]['content'])['required_checklists'] == ['D1']


def test_malformed_review_over_http_preserves_valid_generation(tmp_path):
    """Неполный отчёт API не заменяет полезный ответ на «нет данных»."""
    chunks = make_chunks('training.md', [('Учебный учёт доступен.', None, None)], 1800)
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        raw = ({'status': 'answered', 'answer': 'Учёт доступен.', 'evidence': ['D1']}
               if len(calls) == 1 else {'result': {'status': 'insufficient_data'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(raw)}}]})

    cfg = Settings(tmp_path, base_url='http://unit.test/v1', model='model', api_key='placeholder', retries=0)
    client = QwenClient(cfg, httpx.MockTransport(handler))
    response = EnergytoolCore(cfg, chunks=chunks, client=client).respond(CoreRequest(message='Учёт доступен?'))
    assert len(calls) == 2
    assert response.core_status == 'answered'
    assert response.answer == 'Учёт доступен.'
    assert response.sources
    assert response.warnings == ['Дополнительная проверка ответа не завершена.']


def test_solar_assessment_preparation_question_uses_checklist_intent():
    """Запрос списка исходных данных отделяем от просьбы оценить мощность."""
    from core.dialogue import data_checklist_question

    assert data_checklist_question('Рассматриваем солнечную станцию. Что нужно для оценки?')
    assert not data_checklist_question('Можете оценить мощность станции?')


@pytest.mark.parametrize('draft', [None, 'Рассматриваем солнечную станцию', '900 кВт'])
def test_application_card_after_product_and_power_correction(draft):
    """Карточка сохраняет обе поправки даже при неполном черновике от модели."""
    request = CoreRequest(history=[{'role': 'user', 'content':
        'У нас склад мощностью 600 кВт. Рассматриваем солнечную станцию. Что нужно для оценки?'}],
        message='Исправляю: мощность склада 900 кВт. Солнечную станцию исключаем, '
                'нужен только учёт электроэнергии. Подготовь резюме для специалиста.')
    result = assemble({'status': 'insufficient_data', 'summary_draft': draft}, (), request)
    assert '900 кВт' in result.summary_draft
    assert 'только учёт электроэнергии' in result.summary_draft
    assert '600' not in result.summary_draft
    assert 'солнечн' not in result.summary_draft.lower()


@pytest.mark.parametrize('history,expected,absent', [
    (['Первый склад: 600 кВт; второй склад: 900 кВт.',
      'Поправка: у второго склада 800 кВт.'],
     ['Первый склад: 600 кВт', 'второго склада 800 кВт'], ['900 кВт']),
    (['Солнечную станцию исключаем.', 'Теперь снова нужна солнечная станция.'],
     ['снова нужна солнечная станция'], ['исключаем']),
    (['У нас склад.','Нужен учёт электроэнергии. Подготовь резюме для специалиста.'],
     ['склад', 'учёт электроэнергии'], ['Подготовь']),
])
def test_card_keeps_current_facts(history, expected, absent):
    request = CoreRequest(message=history[-1] if 'Подготовь' in history[-1] else 'Подготовь резюме.',
        history=[{'role': 'user', 'content': text} for text in
                 (history[:-1] if 'Подготовь' in history[-1] else history)])
    draft = assemble({'status': 'insufficient_data'}, (), request).summary_draft
    assert all(value in draft for value in expected)
    assert all(value not in draft for value in absent)


def test_explicit_object_list_for_application_creates_card():
    """Команда из разработческой цепочки явно заказывает черновик заявки."""
    request = CoreRequest(message='Перечисли оба объекта для заявки.', history=[
        {'role': 'user', 'content': 'Объект А имеет 600 кВт, объект Б — 1200 кВт.'}])
    result = assemble({'status': 'insufficient_data'}, (), request)
    assert result.summary_draft
    assert 'Объект А имеет 600 кВт' in result.summary_draft
    assert 'объект Б — 1200 кВт' in result.summary_draft


def test_enumerating_product_conditions_does_not_order_card():
    """Перечисление свойств продукта само по себе не является заказом заявки."""
    request = CoreRequest(message='Перечисли условия подключения станции.')
    result = assemble({'status': 'insufficient_data'}, (), request)
    assert result.summary_draft is None
