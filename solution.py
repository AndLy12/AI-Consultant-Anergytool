"""CSV-адаптер общего ядра: без веб-сервера, аккаунтов и доступа к веб-БД."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
import argparse
import csv
import json
import os
from pathlib import Path
import tempfile
import time

from consultant_core import create_core
from core.config import Settings as CoreSettings
from core.dialogue import parse_question

TECHNICAL_ANSWER = ('Не удалось завершить обработку из-за технического ограничения. '
                    'Это не означает, что в базе нет ответа; повторите запрос после устранения сбоя.')


@dataclass(frozen=True)
class Question:
    """Строковый ID сохраняет ведущие нули и исходную нумерацию."""
    id: str
    text: str


@dataclass(frozen=True)
class Settings:
    """Общий лимит учитывает подготовку индекса, а не только генерацию."""
    data_dir: Path
    questions_path: Path
    output_path: Path
    total_seconds: float = 1140
    max_workers: int = 4

    @classmethod
    def from_environment(cls, questions=None, output=None):
        data = Path(os.getenv('DATA_DIR', 'data')).expanduser().resolve()
        question_path = Path(questions).expanduser().resolve() if questions else data / 'questions.csv'
        if questions and 'DATA_DIR' not in os.environ:
            data = question_path.parent
        try:
            seconds = float(os.getenv('SUBMISSION_TIMEOUT_SECONDS', '1140'))
            workers = int(os.getenv('MAX_WORKERS', '4'))
            if not 0 < seconds <= 1140 or not 1 <= workers <= 16:
                raise ValueError
        except ValueError:
            raise ValueError('Некорректные лимиты запуска') from None
        return cls(data, question_path, Path(output or 'submission.csv').resolve(), seconds, workers)


def load_questions(path: Path) -> list[Question]:
    """CSV-экранирование сохраняет многострочный диалог внутри одного теста."""
    csv.field_size_limit(1_000_000)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(reader.fieldnames) != 2 or set(reader.fieldnames) != {'id', 'question'}:
            raise ValueError('Ожидаются ровно id,question')
        result, seen = [], set()
        for row in reader:
            if None in row or row['id'] is None or row['question'] is None or not row['id'].strip():
                raise ValueError('Некорректная строка входного CSV')
            if row['id'] in seen:
                raise ValueError('Duplicate id: входные ID должны быть уникальны')
            if len(row['question']) > 250000:
                raise ValueError('Слишком длинный тестовый диалог')
            seen.add(row['id'])
            result.append(Question(row['id'], row['question']))
        return result


def write_submission(path: Path, rows):
    """Атомарная запись оставляет готовый CSV даже при прерывании обновления."""
    rows = list(rows)
    seen = set()
    for row_id, answer in rows:
        if row_id in seen or not row_id.strip() or not answer.strip() or len(answer) > 10000:
            raise ValueError('Некорректные данные для submission.csv')
        seen.add(row_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=path.parent,
                                     delete=False, suffix='.tmp') as stream:
        name = stream.name
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(('id', 'answer'))
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def validate_submission(questions_path: Path, output_path: Path):
    """Проверка формата не является оценкой достоверности ответов."""
    expected = [row.id for row in load_questions(questions_path)]
    with output_path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ['id', 'answer']:
            raise ValueError('Ожидаются ровно колонки id,answer')
        rows = list(reader)
    if any(None in row or row.get('answer') is None or not row['answer'].strip()
           or len(row['answer']) > 10000 for row in rows):
        raise ValueError('Пустой, слишком длинный или повреждённый ответ')
    actual = [row['id'] for row in rows]
    if len(set(actual)) != len(actual) or actual != expected:
        raise ValueError('ID не соответствуют исходным строкам')
    return len(rows)


def run(settings: Settings, core=None) -> int:
    """Каждый ID независим; между потоками переиспользуется только индекс."""
    started = time.monotonic()
    deadline = started + settings.total_seconds
    questions = load_questions(settings.questions_path)
    answers = [TECHNICAL_ANSWER for _ in questions]
    output = settings.output_path
    if output.resolve() == settings.questions_path.resolve():
        raise ValueError('Нельзя записывать ответы поверх исходных вопросов')
    write_submission(output, [(q.id, answers[i]) for i, q in enumerate(questions)])
    failures = completed = 0
    owned = core is None
    try:
        if core is None:
            config = replace(CoreSettings.from_environment(), data_dir=settings.data_dir)
            core = create_core(config, deadline=deadline)

        def answer(question):
            if not question.text.strip():
                return 'Пожалуйста, сформулируйте вопрос о задаче и продуктах Energytool.'
            return core.respond(parse_question(question.text), deadline=deadline).answer

        pool = ThreadPoolExecutor(max_workers=settings.max_workers)
        pending, next_index = {}, 0
        try:
            while (next_index < len(questions) or pending) and time.monotonic() < deadline:
                while next_index < len(questions) and len(pending) < settings.max_workers:
                    pending[pool.submit(answer, questions[next_index])] = next_index
                    next_index += 1
                done, _ = wait(pending, timeout=max(0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
                for future in done:
                    index = pending.pop(future)
                    try:
                        text = future.result()
                        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                            raise ValueError
                        answers[index] = text
                    except Exception:
                        failures += 1
                    completed += 1
                if done:
                    write_submission(output, [(q.id, answers[i]) for i, q in enumerate(questions)])
        finally:
            for future in pending:
                future.cancel()
            # Наш HTTP-клиент соблюдает общий deadline. Произвольное стороннее ядро,
            # игнорирующее ограничение времени, этот адаптер принудительно не завершает.
            pool.shutdown(wait=True, cancel_futures=True)
    finally:
        if owned and core is not None:
            core.close()
        report = {'rows': len(questions), 'completed': completed, 'technical_failures': failures,
                  'unprocessed': len(questions) - completed, 'elapsed_seconds': round(time.monotonic() - started, 3)}
        output.with_suffix('.report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    validate_submission(settings.questions_path, output)
    return len(questions)


def main(argv=None):
    """Печатаем только статус и количество строк, без вопросов, ответов и ключей."""
    parser = argparse.ArgumentParser(description='МТС Energytool: CSV-адаптер общего ядра')
    parser.add_argument('--questions')
    parser.add_argument('--output')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_environment(args.questions, args.output)
        count = validate_submission(settings.questions_path, settings.output_path) if args.validate_only else run(settings)
        print(json.dumps({'status': 'format_valid' if args.validate_only else 'finished', 'rows': count}))
        return 0
    except Exception:
        print(json.dumps({'status': 'failed', 'reason': 'check_configuration_input_and_core_report'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
