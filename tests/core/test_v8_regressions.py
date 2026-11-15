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


def test_b09_provider_url_port_is_validated_before_http(tmp_path):
    settings = Settings(tmp_path, base_url='http://localhost:not-a-port/v1',
                        api_key='placeholder', model='model')
    with pytest.raises(CoreUnavailable):
        settings.validate_provider()
