"""Закрытый возобновляемый прогон разработческих входов без передачи критериев модели.

Результат каждого шага сохраняется до следующего вызова. Версии не смешиваются:
возобновление допускается только с теми же исходниками, корпусом и настройками.
Данные и полные контексты должны храниться вне публичного Git.
"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def append(path, value):
    """Сбрасываем завершённую запись на диск, чтобы пережить остановку процесса."""
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def queue(cases):
    """Живые цепочки содержат только user-шаги; фиксированная история сохраняется."""
    items = []
    for case in cases:
        if case.get('split', 'development') != 'development':
            raise ValueError('Допускается только открытый разработческий набор')
        ident = str(case['case_id'])
        turns = case['conversation']
        if not turns or turns[-1]['role'] != 'user':
            raise ValueError('Нет конечного вопроса клиента')
        live = case.get('evaluation_mode') == 'live_chain'
        if live and any(turn['role'] != 'user' for turn in turns):
            raise ValueError('Живая цепочка не должна содержать заранее написанные ответы')
        for step, turn in enumerate(turns if live else [turns[-1]], 1):
            items.append({'id': f'{ident}-t{step}' if live else ident,
                          'parent': ident, 'step': step, 'live': live,
                          'message': turn['content'], 'history': [] if live else turns[:-1]})
    if len({item['id'] for item in items}) != len(items):
        raise ValueError('Повторяющиеся ID')
    return items


def history_for(item, completed):
    """Ответ предыдущей версии/заготовленный transcript не подставляется в live."""
    if not item['live'] or item['step'] == 1:
        return item['history']
    previous = completed.get(f"{item['parent']}-t{item['step'] - 1}")
    if not previous or previous.get('error'):
        raise ValueError('Предыдущий живой шаг не завершён успешно')
    return previous['request']['history'] + [
        {'role': 'user', 'content': previous['request']['message']},
        {'role': 'assistant', 'content': previous['response']['answer']}]


def review_incomplete(row):
    """Штатный контракт сообщает сбой проверки русским предупреждением."""
    if 'response' not in row:
        return None
    warnings = row['response'].get('warnings', [])
    return any(w in warnings for w in ('review_incomplete',
                                      'Дополнительная проверка ответа не завершена.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('code-root', 'cases', 'data-dir', 'output'):
        parser.add_argument('--' + field, type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-steps', type=int, default=10000)
    args = parser.parse_args()
    root = args.code_root.resolve()
    sys.path.insert(0, str(root))
    import httpx
    from core.config import Settings
    from core.engine import EnergytoolCore
    from core.llm import QwenClient
    from core_contract import CoreRequest

    settings = Settings(data_dir=args.data_dir.resolve(),
                        base_url='http://127.0.0.1:1234/v1',
                        api_key='local-verification-placeholder',
                        model='energytool-eval', temperature=0, max_workers=1)
    plan = queue(read_rows(args.cases))
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    runtime = {str(p.relative_to(root)): digest(p) for p in sorted((root / 'core').glob('*.py'))}
    runtime['core_contract.py'] = digest(root / 'core_contract.py')
    corpus = args.data_dir / 'knowledge-base.zip'
    identity = {'code_sha': revision, 'runtime_sha256': runtime,
                'runner_sha256': digest(__file__), 'questions_sha256': digest(args.cases),
                'corpus_sha256': digest(corpus),
                'parameters': {k: str(v) if isinstance(v, Path) else v
                               for k, v in vars(settings).items() if k not in {'api_key', 'base_url'}}}
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    passport = out / 'passport.json'
    if passport.exists():
        if not args.resume or json.loads(passport.read_text(encoding='utf-8'))['identity'] != identity:
            raise ValueError('Для продолжения нужны --resume и прежние исходники/входы/параметры')
    else:
        if any(out.iterdir()):
            raise ValueError('Новый каталог результата должен быть пустым')
        models = httpx.get('http://127.0.0.1:1234/v1/models', timeout=10).json()
        loaded = json.loads(subprocess.check_output(['lms', 'ps', '--json'], text=True))
        matches = [m for m in loaded if m.get('identifier') == settings.model]
        if len(matches) != 1:
            raise ValueError('Фактическая загруженная модель не подтверждена LM Studio')
        passport.write_text(json.dumps({'identity': identity, 'started_at': utc(),
                                       'loaded_model': matches[0],
                                       'provider_models': models, 'planned': len(plan),
                                       'source_alignment': 'pending_independent_review'},
                                      ensure_ascii=False, indent=2), encoding='utf-8')

    active = {'id': None, 'calls': 0, 'attempts': 0}

    def safe(value):
        """В HTTP-журнал не попадают заголовки; известный ключ удаляется и из тела."""
        return json.loads(json.dumps(value, ensure_ascii=False).replace(settings.api_key, '[секрет удалён]'))

    class Transport(httpx.AsyncBaseTransport):
        """Свежий пул на вызов совместим с короткими asyncio-loop штатного клиента."""
        async def handle_async_request(self, request):
            active['attempts'] += 1
            record = {'id': active['id'], 'call': active['calls'],
                      'attempt': active['attempts'], 'started_at': utc(),
                      'payload': json.loads(request.content)}
            start = time.monotonic()
            try:
                async with httpx.AsyncHTTPTransport() as transport:
                    response = await transport.handle_async_request(request)
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 256 * 1024:
                            raise ValueError('Ответ превышает предел журнала и штатного клиента')
                    await response.aclose()
                    record.update(status=response.status_code, body=body.decode('utf-8', errors='replace'))
                    return httpx.Response(response.status_code, content=bytes(body),
                                          headers={'content-type': 'application/json'})
            except BaseException as exc:
                record['error'] = type(exc).__name__
                raise
            finally:
                record['seconds'] = time.monotonic() - start
                append(out / 'network.jsonl', safe(record))

    class Client(QwenClient):
        """Фиксируем реальные контексты всех генераций, проверок и исправлений."""
        def complete(self, messages, deadline, *, response_schema=None):
            active['calls'] += 1
            record = {'id': active['id'], 'call': active['calls'], 'started_at': utc(),
                      'messages': messages, 'response_schema': response_schema}
            append(out / 'calls-started.jsonl', safe(record))
            try:
                value = super().complete(messages, deadline, response_schema=response_schema)
                record['result'] = value
                return value
            except BaseException as exc:
                record['error'] = type(exc).__name__
                raise
            finally:
                append(out / 'calls.jsonl', safe(record))

    result_path = out / 'results.jsonl'
    rows = read_rows(result_path) if result_path.exists() else []
    completed = {row['id']: row for row in rows}
    # Ошибки не теряются и не заменяются успешным повтором под тем же ID.
    core = EnergytoolCore(settings, client=Client(settings, transport=Transport()))
    new = 0
    try:
        for item in plan:
            if item['id'] in completed:
                continue
            if new >= args.max_steps:
                break
            history = history_for(item, completed)
            request = CoreRequest(message=item['message'], history=history)
            active.update(id=item['id'], calls=0, attempts=0)
            row = {'id': item['id'], 'parent_case_id': item['parent'],
                   'step': item['step'], 'live': item['live'],
                   'reset_before': not item['live'] or item['step'] == 1,
                   'request': request.model_dump(), 'started_at': utc()}
            started = time.monotonic()
            try:
                row['response'] = core.respond(request).model_dump()
            except Exception as exc:
                row['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            row.update(seconds=time.monotonic() - started, model_calls=active['calls'],
                       network_attempts=active['attempts'],
                       extra_network_attempts=max(0, active['attempts'] - active['calls']))
            row['review_incomplete'] = review_incomplete(row)
            append(result_path, safe(row))
            append(out / 'sources.jsonl', safe({'id': row['id'], 'step': row['step'],
                   'sources': row.get('response', {}).get('sources'),
                   'availability': 'recorded' if 'response' in row else 'generation_failed'}))
            completed[item['id']] = row
            new += 1
            print(json.dumps({k: row[k] for k in ('id', 'seconds', 'model_calls', 'network_attempts')}, ensure_ascii=False), flush=True)
    finally:
        core.close()
        rows = read_rows(result_path) if result_path.exists() else []
        with (out / 'answers.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['id', 'answer'])
            writer.writerows((r['id'], r['response']['answer']) for r in rows if 'response' in r)
        with (out / 'questions.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['id', 'question'])
            writer.writerows((r['id'], json.dumps(r['request'], ensure_ascii=False)) for r in rows)
        summary = {'completed': len(rows), 'planned': len(plan),
                   'errors': sum('error' in r for r in rows),
                   'pending': [i['id'] for i in plan if i['id'] not in completed],
                   'hashes': {p.name: digest(p) for p in out.iterdir()
                              if p.is_file() and p.name != 'summary.json'}}
        (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return int(summary['errors'] > 0 or bool(summary['pending']))


if __name__ == '__main__':
    raise SystemExit(main())
