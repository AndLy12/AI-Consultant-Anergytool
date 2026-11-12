"""Актуальный CLI общего ядра: строгий CSV и явный demo, без настоящего API.

Ранние проверки query/text/message и OUTPUT_PATH относились к заменённому
baseline. Теперь проверяем контракт Данька, не добавляя совместимость в его код.
"""

import csv
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def invoke(tmp_path, rows, *arguments):
    data = tmp_path / 'data'
    data.mkdir(exist_ok=True)
    source = data / 'questions.csv'
    with source.open('w',encoding='utf-8',newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['id','question'])
        writer.writerows(rows)
    output = tmp_path / 'submission.csv'
    env = {k:v for k,v in os.environ.items() if not k.startswith(('LLM_', 'OPENAI_', 'CORE_'))
           and k not in {'DATA_DIR','GEN_MODEL','CHUNKER','MAX_WORKERS','SUBMISSION_TIMEOUT_SECONDS'}}
    env.update(CORE_DEMO='true',DATA_DIR=str(data))
    result = subprocess.run([sys.executable,str(ROOT/'solution.py'),'--output',str(output),*arguments],
                            cwd=tmp_path,env=env,text=True,capture_output=True,timeout=20)
    return result, source, output


def test_actual_cli_demo_and_independent_rows(tmp_path):
    result, source, output = invoke(tmp_path,[('001','Как экспортировать отчёт стенда Альфа в CSV?'),('002','')])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['rows'] == 2
    with output.open(encoding='utf-8',newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert [r['id'] for r in rows] == ['001','002']
    assert 'Демонстрационный режим.' in rows[0]['answer']
    assert 'сформулируйте' in rows[1]['answer']


def test_cli_validation_and_repeat_run(tmp_path):
    result, source, output = invoke(tmp_path,[('0001','Экспорт CSV')])
    assert result.returncode == 0
    again, _, _ = invoke(tmp_path,[('0001','Экспорт CSV')],'--validate-only')
    assert again.returncode == 0
    assert json.loads(again.stdout)['status'] == 'format_valid'


def test_invalid_csv_is_diagnostic(tmp_path):
    result, _, _ = invoke(tmp_path,[('1','Первый'),('1','Второй')])
    assert result.returncode == 2
    assert 'Traceback' not in result.stderr
    assert json.loads(result.stdout)['status'] == 'failed'


def test_header_only_cli(tmp_path):
    result, _, output = invoke(tmp_path,[])
    assert result.returncode == 0
    assert output.read_text(encoding='utf-8') == 'id,answer\n'
