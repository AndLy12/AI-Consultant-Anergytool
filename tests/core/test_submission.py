"""CSV и плоская поставка: проверяем чистый запуск и независимость от веба."""

import csv
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

from consultant_core import create_core
from core.config import Settings as CoreSettings
from core_contract import CoreResponse, CoreUnavailable
from scripts.build_core_submission import build
import solution


def questions(path, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(('id', 'question'))
        writer.writerows(rows)


def test_mixed_independent_dialogues_csv_and_unicode(tmp_path):
    source = tmp_path / 'questions.csv'
    rows = [('001', 'Пользователь: Стенд Альфа\nАссистент: Уточните\nПользователь: Как экспортировать CSV?'),
            ('002', 'Прогноз погоды'), ('003', '')]
    questions(source, rows)
    target = tmp_path / 'submission.csv'
    core = create_core(CoreSettings(data_dir=tmp_path, demo=True))
    assert solution.run(solution.Settings(tmp_path, source, target), core) == 3
    assert solution.validate_submission(source, target) == 3
    with target.open(encoding='utf-8', newline='') as stream:
        results = list(csv.DictReader(stream))
    assert [row['id'] for row in results] == ['001', '002', '003']
    assert 'Демонстрационный режим.' in results[0]['answer']
    assert 'недостаточно' in results[1]['answer']
    assert 'сформулируйте' in results[2]['answer']


@pytest.mark.parametrize('row', [('a', ''), ('a', 'x' * 10001)])
def test_output_never_silently_truncates(row, tmp_path):
    with pytest.raises(ValueError):
        solution.write_submission(tmp_path / 'output.csv', [row])


def test_validator_detects_extra_and_missing_ids(tmp_path):
    source = tmp_path / 'questions.csv'
    target = tmp_path / 'submission.csv'
    questions(source, [('0001', 'Вопрос')])
    for content in ['id,answer,extra\n0001,Ответ,x\n', 'id,answer\n1,Ответ\n',
                    'id,answer\n0001,Ответ\n0001,Ответ\n', 'id,answer\n0001,\n']:
        target.write_text(content, encoding='utf-8')
        with pytest.raises(ValueError):
            solution.validate_submission(source, target)


def test_failed_initialization_still_leaves_all_ids(tmp_path, monkeypatch):
    source = tmp_path / 'questions.csv'
    target = tmp_path / 'submission.csv'
    questions(source, [('1', 'Вопрос'), ('2', 'Другой вопрос')])
    monkeypatch.setenv('CORE_DEMO', 'false')
    with pytest.raises(CoreUnavailable):
        solution.run(solution.Settings(tmp_path, source, target))
    assert solution.validate_submission(source, target) == 2
    assert 'технического ограничения' in target.read_text(encoding='utf-8')


def test_one_failure_does_not_lose_other_rows(tmp_path):
    source, target = tmp_path / 'questions.csv', tmp_path / 'submission.csv'
    questions(source, [('a', 'Успех'), ('b', 'Сбой')])
    class Core:
        def respond(self, request, **options):
            if request.message == 'Сбой':
                raise CoreUnavailable('Тестовая ошибка')
            return CoreResponse(answer='Успешный тестовый ответ')
    solution.run(solution.Settings(tmp_path, source, target), Core())
    assert solution.validate_submission(source, target) == 2
    report = json.loads(target.with_suffix('.report.json').read_text())
    assert report['technical_failures'] == 1
    assert 'Успешный тестовый ответ' in target.read_text(encoding='utf-8')


def test_global_deadline_and_prefilled_ids(tmp_path):
    source, target = tmp_path / 'questions.csv', tmp_path / 'submission.csv'
    questions(source, [(str(i), 'Вопрос') for i in range(8)])
    class SlowCore:
        def respond(self, request, *, deadline):
            while time.monotonic() < deadline:
                time.sleep(.005)
            raise CoreUnavailable('Дедлайн')
    start = time.monotonic()
    solution.run(solution.Settings(tmp_path, source, target, total_seconds=.06, max_workers=2), SlowCore())
    assert time.monotonic() - start < 1
    assert solution.validate_submission(source, target) == 8


def test_flat_archive_runs_without_backend_or_core_package(tmp_path):
    archive = tmp_path / 'candidate.zip'
    names = build(archive)
    assert all('/' not in name and '\\' not in name for name in names)
    assert not any(name.endswith(('.db', '.json', '.md')) for name in names)
    isolated = tmp_path / 'isolated'
    isolated.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(isolated)
    source = isolated / 'questions.csv'
    questions(source, [('0001', 'Экспорт отчёта CSV')])
    env = dict(os.environ, CORE_DEMO='true', PYTHONPATH='')
    result = subprocess.run([sys.executable, 'solution.py', '--questions', str(source)],
                            cwd=isolated, env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert solution.validate_submission(source, isolated / 'submission.csv') == 1
    assert not (isolated / 'core').exists() and not (isolated / 'backend').exists()
