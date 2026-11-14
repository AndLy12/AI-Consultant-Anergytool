"""Проверки актуального черновика и устойчивости необязательного прохода модели."""

import logging
import json

import pytest

from core.config import Settings
from core.documents import make_chunks
from core.engine import EnergytoolCore
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
