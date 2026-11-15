"""Новые воспроизведения ошибок V8 от 00f5895; проверки выполняются без внешнего API."""

import csv
import json
import time
from pathlib import Path

import httpx
import pytest

import solution
from core.config import Settings
from core.llm import QwenClient
from core_contract import CoreResponse, CoreUnavailable, ModelResponseError, ProviderError


def write_questions(path, rows):
    """Создаём самостоятельный вход с точными строковыми ID."""
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['id', 'question'])
        writer.writerows(rows)


class LocalCore:
    """Минимальный локальный контракт вместо модели."""
    def respond(self, request, **kwargs):
        return CoreResponse(answer='Локальный проверочный ответ.')


def test_b01_report_must_not_overwrite_question_source(tmp_path):
    source = tmp_path / 'answers.report.json'
    output = tmp_path / 'answers.csv'
    write_questions(source, [('001', 'Вопрос')])
    original = source.read_bytes()
    with pytest.raises(ValueError):
        solution.run(solution.Settings(tmp_path, source, output), LocalCore())
    assert source.read_bytes() == original


def test_b02_empty_csv_does_not_initialize_provider(tmp_path, monkeypatch):
    source, output = tmp_path / 'questions.csv', tmp_path / 'answers.csv'
    write_questions(source, [])
    def forbidden(*args, **kwargs):
        pytest.fail('Пустой вход не должен создавать модель')
    monkeypatch.setattr(solution.CoreSettings, 'from_environment', forbidden)
    assert solution.run(solution.Settings(tmp_path, source, output)) == 0
    assert solution.validate_submission(source, output) == 0


def test_b03_failed_atomic_write_removes_temporary_file(tmp_path, monkeypatch):
    output = tmp_path / 'answers.csv'
    output.write_text('прежний результат', encoding='utf-8')
    def disk_full(*args):
        raise OSError('Имитация заполненного диска')
    monkeypatch.setattr(solution.os, 'fsync', disk_full)
    with pytest.raises(OSError):
        solution.write_submission(output, [('001', 'Ответ')])
    assert output.read_text(encoding='utf-8') == 'прежний результат'
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('reason', ['content_filter', 'tool_calls'])
def test_b04_noncompleted_finish_reason_is_not_accepted(tmp_path, reason):
    def handler(request):
        return httpx.Response(200, json={'choices': [{'finish_reason': reason,
            'message': {'content': '{"status":"insufficient_data"}'}}]})
    client = QwenClient(Settings(tmp_path, model='model', base_url='http://unit.test/v1',
                                api_key='placeholder'), httpx.MockTransport(handler))
    with pytest.raises(ModelResponseError):
        client.complete([], time.monotonic() + 2)


def test_b05_invalid_model_does_not_disable_provider_options(tmp_path):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={'error': {'param': 'model', 'message': 'Unknown model'}})
    client = QwenClient(Settings(tmp_path, model='model', base_url='http://unit.test/v1',
                                api_key='placeholder'), httpx.MockTransport(handler))
    with pytest.raises(CoreUnavailable):
        client.complete([], time.monotonic() + 2)
    assert len(calls) == 1
    assert client.use_json_mode and client.use_reasoning_effort and client.use_thinking_option


@pytest.mark.parametrize('power,energy,canonical', [
    ('киловатт', 'киловаттчасов', 'кВт·ч'),
    ('мегаватт', 'мегаватт-часов', 'МВт·ч'),
    ('мегаватт', 'мегаватт часов', 'МВт·ч'),
])
def test_b06_spelled_energy_is_not_normalized_as_power(power, energy, canonical):
    from core.retrieval import tokens
    assert tokens(energy) == tokens(canonical)
    assert tokens(energy) != tokens(power)


def test_b07_expired_preparation_deadline_prevents_file_read(tmp_path, monkeypatch):
    from core.documents import load_chunks
    source = tmp_path / 'data.md'
    source.write_text('# База\n\nСведения.', encoding='utf-8')
    calls = []
    original = Path.read_bytes
    def read(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    with pytest.raises(CoreUnavailable):
        load_chunks(Settings(tmp_path, kb_path=source), deadline=time.monotonic() - 1)
    assert not calls


def test_b08_markdown_adjacent_headings_keep_separate_sections():
    from core.documents import make_chunks
    chunks = make_chunks('product.md', [('# Продукт\n## Гарантия\nУсловия гарантии.\n'
        '## Обслуживание\nУсловия обслуживания.', None, None)], 1800)
    assert {c.section for c in chunks} == {'Гарантия', 'Обслуживание'}
    assert all('Условия обслуживания' not in c.text for c in chunks if c.section == 'Гарантия')


@pytest.mark.parametrize('url', ['http://localhost:not-a-port/v1', 'http://localhost:65536/v1', 'http://localhost:0/v1'])
def test_b09_provider_url_port_is_validated_before_http(tmp_path, url):
    settings = Settings(tmp_path, base_url=url,
                        api_key='placeholder', model='model')
    with pytest.raises(CoreUnavailable):
        settings.validate_provider()


def test_b10_fenced_markdown_is_not_document_structure():
    from core.documents import make_chunks
    text = ('```markdown\n# Чужой заголовок\n## Пример\nДанные примера.\n```\n\n'
            '# Настоящий продукт\n\n## Гарантия\n\nНастоящие условия.')
    chunks = make_chunks('product.md', [(text, None, None)], 1800)
    assert all(c.title == 'Настоящий продукт' for c in chunks)
    assert all(c.section != 'Пример' for c in chunks)
    assert any(c.section == 'Гарантия' for c in chunks)


def test_b11_zero_chunk_size_fails_instead_of_hanging():
    import subprocess
    import sys
    # Отдельный процесс нужен, чтобы прежний бесконечный цикл не блокировал pytest.
    code = ('from core.documents import make_chunks\n'
            'try:\n make_chunks("x.md", [("Данные",None,None)], 0)\n'
            'except (ValueError, TypeError):\n print("rejected")\n')
    try:
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail('Нулевой размер чанка вызвал бесконечный цикл')
    assert result.returncode == 0 and result.stdout.strip() == 'rejected'


def test_b12_oversized_reranker_response_is_discarded(tmp_path, monkeypatch):
    import core.llm as llm
    monkeypatch.setattr(llm, 'MAX_RESPONSE_BYTES', 100)
    def handler(request):
        return httpx.Response(200, json={'results': [{'index': 0}], 'padding': 'x' * 200})
    client = QwenClient(Settings(tmp_path, model='model', base_url='http://unit.test/v1',
                                api_key='placeholder', reranker_model='reranker'), httpx.MockTransport(handler))
    assert client.rerank('вопрос', ['текст'], 1, time.monotonic() + 2) == []


@pytest.mark.parametrize('original,draft', [
    ('Мощность 1900 кВт.', '900 кВт'),
    ('Мощность 1 900 кВт.', '900 кВт'),
    ('Мощность около 900 кВт.', '900 кВт'),
    ('Потребление 900 кВт·ч в месяц.', '900 кВт'),
])
def test_b13_summary_cannot_cut_value_or_unit_context(original, draft):
    from core.validation import assemble
    from core_contract import CoreRequest
    response = assemble({'status': 'insufficient_data', 'summary_draft': draft}, (),
        CoreRequest(message='Подготовь резюме.', history=[{'role': 'user', 'content': original}]))
    assert response.summary_draft != draft
    assert original in response.summary_draft


def test_b14_csv_answer_keeps_verified_card_despite_summary_heading():
    from core.documents import make_chunks
    from core.validation import assemble
    from core_contract import CoreRequest
    chunks = make_chunks('example.md', [('Для заявки нужны исходные сведения.', None, None)], 1800)
    response = assemble({'status': 'answered', 'answer': 'Резюме: данные уточнены.', 'evidence': ['D1']},
        chunks, CoreRequest(message='Исправляю: мощность 900 кВт. Подготовь резюме.'))
    assert response.summary_draft and response.summary_draft in response.answer


def test_b15_prefix_correction_keeps_named_object_identity():
    from core.validation import assemble
    from core_contract import CoreRequest
    request = CoreRequest(message='Подготовь резюме.', history=[
        {'role': 'user', 'content': 'Объект А имеет 600 кВт, объект Б — 900 кВт.'},
        {'role': 'user', 'content': 'Поправка: объект А имеет 1200 кВт.'}])
    draft = assemble({'status': 'insufficient_data'}, (), request).summary_draft
    assert '1200 кВт' in draft and '900 кВт' in draft
    assert '600 кВт' not in draft


@pytest.mark.parametrize('value', ['-900', '−900', '+900'])
def test_b16_signed_customer_quantity_is_preserved(value):
    from core.validation import assemble
    from core_contract import CoreRequest
    request = CoreRequest(message='Подготовь резюме.', history=[
        {'role': 'user', 'content': 'Исправляю: потребление '+value+' кВт·ч в месяц.'}])
    draft = assemble({'status': 'insufficient_data'}, (), request).summary_draft
    assert value+' кВт·ч' in draft


@pytest.mark.parametrize('key', ['ключ', 'abc\tdef', 'abc def', 'abc\x7fdef'])
def test_b17_invalid_authorization_header_fails_configuration(tmp_path, key):
    config = Settings(tmp_path, base_url='http://unit.test/v1', api_key=key, model='model')
    with pytest.raises(CoreUnavailable):
        config.validate_provider()


@pytest.mark.parametrize('change', [{'max_workers': 0}, {'request_seconds': float('nan')},
                                   {'retries': -1}, {'context_chars': float('inf')}])
def test_b18_programmatic_settings_reject_invalid_runtime_limits(tmp_path, change):
    with pytest.raises(CoreUnavailable):
        Settings(tmp_path, **change)


def test_b19_rejected_price_category_is_not_required_from_history():
    from core.documents import make_chunks
    from core.retrieval import SearchIndex
    from core_contract import CoreRequest
    chunks = make_chunks('categories.md', [('# Ценовые категории\n\n'
        'Третья: почасовой учёт.\n\nШестая: планирование.', None, None)], 1800)
    request = CoreRequest(message='Нет, не третья категория. Номер ещё уточняю.', history=[
        {'role': 'user', 'content': 'У нас третья ценовая категория.'}])
    hits = SearchIndex(tuple(chunks)).search_request(request)
    assert not any(h.required and h.chunk.text.startswith('Третья') for h in hits)


def test_b20_control_panel_is_not_solar_product():
    from core.dialogue import search_intent
    from core_contract import CoreRequest
    intent = search_intent(CoreRequest(message='Где панель управления облачным учётом?'))
    assert 'облачная платформа' in intent.active
    assert 'солнечные электростанции' not in intent.active


def test_b21_spelled_meter_count_correction_removes_old_quantity():
    from core.validation import assemble
    from core_contract import CoreRequest
    request = CoreRequest(message='Подготовь резюме.', history=[
        {'role': 'user', 'content': 'У нас шесть приборов.'},
        {'role': 'user', 'content': 'Исправляю: десять приборов.'}])
    draft = assemble({'status': 'insufficient_data'}, (), request).summary_draft
    assert 'десять приборов' in draft
    assert 'шесть' not in draft


def test_b22_secrets_are_checked_before_json_escaping():
    from core.documents import make_chunks
    from core.prompts import build_messages
    from core.validation import assemble
    from core_contract import CoreRequest
    secret = 'key"with\\escape'
    chunks = make_chunks('x.md', [('Данные доступны.', None, None)], 1800)
    request = CoreRequest(message='Секрет '+secret)
    payload = json.loads(build_messages(request, [], chunks, secret)[1]['content'])
    assert secret not in payload['current_message']
    with pytest.raises(CoreUnavailable):
        assemble({'status': 'answered', 'answer': 'Секрет '+secret, 'evidence': ['D1']},
                 chunks, request, secret=secret)


def test_b23_surrogate_json_text_is_rejected_safely(tmp_path):
    from core.documents import load_chunks
    from core.validation import assemble
    from core_contract import CoreRequest
    source = tmp_path / 'data.json'
    source.write_text('{"text":"private-\\ud800"}', encoding='utf-8')
    with pytest.raises(CoreUnavailable):
        load_chunks(Settings(tmp_path, kb_path=source))
    with pytest.raises(ModelResponseError):
        assemble({'status': 'clarification_needed', 'question': 'private-\ud800'}, (), CoreRequest(message='Вопрос'))


def test_b24_nonstandard_http_status_is_not_retried_as_5xx(tmp_path, monkeypatch):
    import asyncio
    calls = []
    async def no_delay(*args):
        return None
    monkeypatch.setattr(asyncio, 'sleep', no_delay)
    def handler(request):
        calls.append(request)
        return httpx.Response(600)
    client = QwenClient(Settings(tmp_path, model='model', base_url='http://unit.test/v1',
                                api_key='placeholder', retries=2), httpx.MockTransport(handler))
    with pytest.raises(CoreUnavailable):
        client.complete([], time.monotonic() + 2)
    assert len(calls) == 1


def test_b25_compatibility_negotiation_does_not_reset_network_retries(tmp_path, monkeypatch):
    import asyncio
    calls = []
    async def no_delay(*args):
        return None
    monkeypatch.setattr(asyncio, 'sleep', no_delay)
    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(400, text='Unsupported reasoning_effort')
        if len(calls) in (1, 3):
            return httpx.Response(500)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    client = QwenClient(Settings(tmp_path, model='model', base_url='http://unit.test/v1',
                                api_key='placeholder', retries=1), httpx.MockTransport(handler))
    with pytest.raises(ProviderError):
        client.complete([], time.monotonic() + 2)
    assert len(calls) == 3


def test_b26_csv_knowledge_does_not_depend_on_global_reader_limit(tmp_path):
    from core.documents import read_blocks
    old = csv.field_size_limit()
    try:
        csv.field_size_limit(100)
        blocks = read_blocks('knowledge.csv', ('header\n' + 'x' * 200000 + '\n').encode())
        assert 'x' * 200000 in blocks[0][0]
        assert csv.field_size_limit() == 100
        source = tmp_path / 'questions.csv'
        write_questions(source, [('001', 'y' * 200)])
        assert solution.load_questions(source)[0].text == 'y' * 200
        assert csv.field_size_limit() == 100
    finally:
        csv.field_size_limit(old)


def test_b27_deep_reranker_json_preserves_local_fallback(tmp_path):
    def handler(request):
        body = '{"results":[],"nested":' + '[' * 10000 + '0' + ']' * 10000 + '}'
        return httpx.Response(200, text=body)
    client = QwenClient(Settings(tmp_path, model='model', base_url='http://unit.test/v1',
                                api_key='placeholder', reranker_model='reranker'), httpx.MockTransport(handler))
    assert client.rerank('вопрос', ['текст'], 1, time.monotonic() + 2) == []


@pytest.mark.parametrize('qualifier', ['приблизительно', 'ориентировочно', 'кажется', 'возможно'])
def test_b28_summary_keeps_client_uncertainty(qualifier):
    from core.validation import assemble
    from core_contract import CoreRequest
    request = CoreRequest(message='Подготовь резюме.', history=[
        {'role': 'user', 'content': 'Исправляю: '+qualifier+' 900 кВт.'}])
    draft = assemble({'status': 'insufficient_data'}, (), request).summary_draft
    assert qualifier+' 900 кВт' in draft


def test_b29_object_ordinal_is_not_a_price_category():
    from core.retrieval import price_category
    assert price_category('На второй площадке установлен учёт.') is None
    assert price_category('У третьего объекта другая схема.') is None


def test_b30_skipped_heading_level_does_not_nest_siblings():
    from core.documents import make_chunks
    text = '# Продукт\n\n### Гарантия\n\nУсловие гарантии.\n\n### Обслуживание\n\nУсловие обслуживания.'
    chunks = make_chunks('product.md', [(text, None, None)], 1800)
    second = next(c for c in chunks if c.section == 'Обслуживание')
    assert second.section_path == ('Продукт', 'Обслуживание')


def test_b07_deadline_is_checked_after_last_document_parse(tmp_path, monkeypatch):
    """Последний документ не может вернуть успешную подготовку после дедлайна."""
    import core.documents as documents
    from types import SimpleNamespace
    source = tmp_path / 'data.md'
    source.write_text('Сведения базы.', encoding='utf-8')
    clock = [0]
    monkeypatch.setattr(documents, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    original = documents.read_blocks
    def parse(name, content):
        result = original(name, content)
        clock[0] = 11
        return result
    monkeypatch.setattr(documents, 'read_blocks', parse)
    with pytest.raises(CoreUnavailable):
        documents.load_chunks(Settings(tmp_path, kb_path=source), deadline=10)


def test_b22_review_does_not_receive_discarded_draft_secret(tmp_path):
    from core.engine import EnergytoolCore
    from core.documents import make_chunks
    from core_contract import CoreRequest
    secret = 'private"key'
    chunks = make_chunks('data.md', [('Учёт доступен.', None, None)], 1800)
    class Client:
        def complete(self, messages, deadline):
            return {'status': 'answered', 'answer': 'Учёт доступен.', 'evidence': ['D1'],
                    'summary_draft': secret}
        def review(self, messages, deadline):
            payload = json.loads(messages[-1]['content'])
            assert secret not in payload['draft']['summary_draft']
            return {'issues': [], 'follow_up_question': None,
                    'result': {'status': 'answered', 'answer': 'Учёт доступен.', 'evidence': ['D1']}}
    settings = Settings(tmp_path, model='model', base_url='http://unit.test/v1', api_key=secret)
    result = EnergytoolCore(settings, chunks=chunks, client=Client()).respond(CoreRequest(message='Учёт доступен?'))
    assert result.core_status == 'answered'
