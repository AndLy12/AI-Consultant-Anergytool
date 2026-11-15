"""Защита живого QA от подмены истории и повторного использования чужих ответов."""

import pytest
from scripts.check_core_development import history_for, queue, review_incomplete


def test_live_history_uses_actual_previous_answer_and_keeps_zero_id():
    cases = [{'case_id': '007', 'evaluation_mode': 'live_chain', 'conversation': [
        {'role': 'user', 'content': 'Первый вопрос'},
        {'role': 'user', 'content': 'Второй вопрос'}]}]
    items = queue(cases)
    assert [r['id'] for r in items] == ['007-t1', '007-t2']
    assert history_for(items[0], {}) == []
    previous = {'request': {'message': 'Первый вопрос', 'history': []},
                'response': {'answer': 'Фактический ответ версии'}}
    assert history_for(items[1], {'007-t1': previous}) == [
        {'role': 'user', 'content': 'Первый вопрос'},
        {'role': 'assistant', 'content': 'Фактический ответ версии'}]
    with pytest.raises(ValueError):
        history_for(items[1], {'007-t1': {'error': 'timeout'}})


def test_fixed_history_is_not_replaced_and_hidden_split_is_rejected():
    case = {'case_id': '01', 'conversation': [
        {'role': 'assistant', 'content': 'Фиксированный контекст'},
        {'role': 'user', 'content': 'Вопрос'}]}
    item = queue([case])[0]
    assert history_for(item, {}) == case['conversation'][:-1]
    with pytest.raises(ValueError):
        queue([dict(case, split='final')])
    with pytest.raises(ValueError):
        queue([dict(case, evaluation_mode='live_chain')])
    with pytest.raises(ValueError):
        queue([case, case])


def test_russian_review_warning_is_not_reported_as_successful_review():
    assert review_incomplete({'response': {'warnings': [
        'Дополнительная проверка ответа не завершена.']}}) is True
    assert review_incomplete({'response': {'warnings': []}}) is False
    assert review_incomplete({'error': {'type': 'ProviderError'}}) is None
