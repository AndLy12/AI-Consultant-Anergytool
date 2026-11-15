"""Журнал содержательной приёмки: реальные ответы, происхождение и ручная оценка.

По умолчанию формируется очередь без вызовов модели. --execute требует
настоящего провайдера; demo и подстановка ответов вместо недоступного API
запрещены. Продуктовые тексты, ответы и сырая структура остаются в data/.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / 'knowledge-base-work/data/review'
sys.path.insert(0, str(ROOT))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def redact(value, secret: str):
    """Удаляет действующий ключ также из сырого результата и ошибок провайдера."""
    if isinstance(value, str):
        return value.replace(secret, '[секрет удалён]') if secret else value
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    if isinstance(value, dict):
        return {redact(str(key), secret): redact(item, secret) for key, item in value.items()}
    return value


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    path.chmod(0o600)


def read_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def fingerprint(case: dict) -> str:
    """Одинаковые входы не выдаются за разные независимые вопросы."""
    data = json.dumps(case['conversation'], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def build_queue(review: Path, suite: str, repeats: int, parse_question) -> list[dict]:
    """Читает существующую разметку; независимый набор не используется для настройки."""
    if not 1 <= repeats <= 5:
        raise ValueError('Число критических повторов должно быть от 1 до 5')
    suites = ('critical', 'main', 'demo', 'holdout', 'live') if suite == 'all' else (suite,)
    queue = []
    for selected in suites:
        if selected in {'critical', 'smoke'}:
            cases = read_cases(review / 'role3-critical.jsonl')
            if selected == 'smoke':
                cases = cases[:1]
        elif selected == 'main':
            cases = read_cases(review / 'cases.jsonl')
        elif selected == 'holdout':
            frozen = json.loads((review / 'role3-holdout-freeze.json').read_text(encoding='utf-8'))
            if sha256(review / 'role3-holdout.jsonl') != frozen['sha256']:
                raise ValueError('Независимый набор изменился после фиксации')
            cases = read_cases(review / 'role3-holdout.jsonl')
        elif selected == 'live':
            cases = read_cases(review / 'role3-live.jsonl')
        elif selected == 'demo':
            with (review.parent / 'evaluation/demo_questions.csv').open(encoding='utf-8-sig', newline='') as stream:
                cases = []
                for row in csv.DictReader(stream):
                    request = parse_question(row['question'])
                    conversation = [turn.model_dump() for turn in request.history]
                    conversation.append({'role': 'user', 'content': request.message})
                    cases.append({'case_id': row['id'], 'conversation': conversation,
                                  'category': 'official_demo', 'expected_action': 'human_review',
                                  'comment': 'Пример ответа не является скрытым golden-эталоном.'})
        else:
            raise ValueError('Неизвестный набор')
        if len({case['case_id'] for case in cases}) != len(cases):
            raise ValueError('В наборе повторяются ID')
        for case in cases:
            for repeat in range(1, (repeats if selected == 'critical' else 1) + 1):
                queue.append({'attempt_id': f"{selected}-{case['case_id']}-r{repeat}",
                              'suite': selected, 'repeat': repeat, 'case': case,
                              'input_sha256': fingerprint(case)})
    return queue


class ObservedClient:
    """Сохраняет штатные вход и результат клиента, не обращаясь к другому API.

    Управляемая вредоносная строка может добавляться только в копию JSON
    документов по отдельному флагу. Системная инструкция и файлы не меняются.
    """
    def __init__(self, delegate):
        self.delegate = delegate
        self.reset()

    def reset(self, fixture=None):
        self.observation = {'model_call_started': False, 'model_call_completed': False,
                            'context_sent': None, 'raw_selection': None}
        self.fixture = fixture

    def complete(self, messages, deadline):
        copied = deepcopy(messages)
        payload = json.loads(copied[-1]['content'])
        if self.fixture:
            if not payload.get('documents'):
                raise ValueError('Нет контекста для управляемого теста')
            payload['documents'][0]['text'] += '\n\n' + self.fixture['text']
            copied[-1]['content'] = json.dumps(payload, ensure_ascii=False)
        self.observation['context_sent'] = payload
        self.observation['model_call_started'] = True
        result = self.delegate.complete(copied, deadline)
        self.observation['model_call_completed'] = True
        self.observation['raw_selection'] = result
        return result

    def close(self):
        self.delegate.close()


def collect(queue: list[dict], *, invoke=None, observed=None, secret='',
            blocker=None, allow_fixture=False, budget_seconds=1800, record_callback=None) -> list[dict]:
    """Выполняет попытки последовательно; ошибка API не порождает выдуманный ответ.

    Историю живой цепочки составляют только полученные ответы. При ошибке
    зависимые шаги блокируются, а не заменяются заранее написанной историей.
    """
    result, histories, broken = [], {}, set()
    stopped = False
    deadline = time.monotonic() + budget_seconds
    def remember(row):
        safe = redact(row, secret)
        result.append(safe)
        if record_callback:
            record_callback(safe)
    for item in queue:
        case = item['case']
        chain = case.get('live_chain')
        if chain and case.get('reset_before'):
            histories[chain] = []
            broken.discard(chain)
        conversation = deepcopy(case['conversation'])
        if chain:
            conversation = deepcopy(histories.get(chain, [])) + conversation[-1:]
        row = {**item, 'effective_conversation': conversation,
               'history_mode': 'live_model_answers' if chain else 'seeded' if len(conversation) > 1 else 'none',
               'status': 'blocked', 'blocked_reason': None, 'answer': None,
               'core_response': None, 'source_provenance_verified': None,
               'model_call_started': False, 'model_call_completed': False,
               'context_sent': None, 'raw_selection': None, 'elapsed_ms': 0,
               'semantic_assessment': 'not_reviewed'}
        reason = blocker
        if reason is None and stopped:
            reason = 'stopped_after_technical_failure'
        if reason is None and time.monotonic() >= deadline:
            reason = 'run_time_budget_exhausted'
        if reason is None and chain in broken:
            reason = 'previous_live_step_failed'
        if reason is None and case.get('context_fixture') and not allow_fixture:
            reason = 'controlled_context_test_requires_agreement'
        if reason:
            row['blocked_reason'] = reason
            remember(row)
            continue
        started = time.monotonic()
        if observed:
            observed.reset(case.get('context_fixture'))
        try:
            response = invoke(conversation, deadline)
            value = response.model_dump() if hasattr(response, 'model_dump') else response
            row['core_response'] = value
            row['answer'] = value['answer']
            if observed:
                row.update(observed.observation)
            row['status'] = 'obtained_pending_review' if row['model_call_completed'] else 'local_response_without_model'
            sent = {doc['chunk_id']: doc['text'] for doc in (row['context_sent'] or {}).get('documents', [])}
            sources = value.get('sources', [])
            row['source_provenance_verified'] = all(source['chunk_id'] in sent and source['excerpt'] in sent[source['chunk_id']] for source in sources) if sources else None
            if chain:
                histories[chain] = conversation + [{'role': 'assistant', 'content': value['answer']}]
        except Exception as exc:
            row['status'] = 'technical_failure'
            row['error_type'] = type(exc).__name__
            # Сначала удаляем ключ: обрезка могла бы оставить его открытый префикс.
            row['error'] = redact(str(exc), secret)[:500]
            if observed:
                row.update(observed.observation)
            if chain:
                broken.add(chain)
            stopped = True
        row['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
        remember(row)
    return result


def summarize(rows: list[dict]) -> dict:
    """Нет ручных оценок — нет процента успешно принятых консультаций."""
    from collections import Counter
    return {'planned_attempts': len(rows), 'unique_seeded_inputs': len({r['input_sha256'] for r in rows}),
            'statuses': dict(Counter(row['status'] for row in rows)),
            'model_calls_started': sum(row['model_call_started'] for row in rows),
            'model_calls_completed': sum(row['model_call_completed'] for row in rows),
            'semantic_passed': 0, 'semantic_failed': 0,
            'semantic_not_reviewed': len(rows), 'acceptance_decision': 'not_evaluated'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Паспорт, очередь и реальные ответы роли 3 без автоматической подмены оценок')
    parser.add_argument('--review', type=Path, default=REVIEW)
    parser.add_argument('--suite', choices=['smoke', 'critical', 'main', 'demo', 'holdout', 'live', 'all'], default='all')
    parser.add_argument('--critical-repeats', type=int, default=3)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--include-context-fixtures', action='store_true')
    parser.add_argument('--budget-seconds', type=int, default=1800)
    parser.add_argument('--source-revision', default='not_specified')
    parser.add_argument('--criteria-status', choices=['pending_team_confirmation', 'user_confirmed_preliminary'],
                        default='pending_team_confirmation')
    args = parser.parse_args(argv)
    if not 1 <= args.budget_seconds <= 14400:
        parser.error('Бюджет прогона должен быть от 1 до 14400 секунд')
    from core.config import Settings
    from core.dialogue import parse_question
    from core.prompts import PROMPT_VERSION
    now = datetime.now(ZoneInfo('Europe/Moscow'))
    run_id = now.strftime('role3-%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8]
    out = args.review / 'runs' / run_id
    if not out.resolve().is_relative_to((ROOT / 'knowledge-base-work/data').resolve()):
        parser.error('Доказательства прогона должны оставаться в игнорируемой data/')
    queue = build_queue(args.review, args.suite, args.critical_repeats, parse_question)
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    groups = [('LLM_BASE_URL', 'OPENAI_BASE_URL'), ('LLM_API_KEY', 'OPENAI_API_KEY'), ('LLM_MODEL', 'OPENAI_MODEL', 'GEN_MODEL')]
    missing = ['/'.join(names) for names in groups if not any(os.getenv(n, '').strip() for n in names)]
    blocker, core, observed, secret = None, None, None, ''
    config = Settings(data_dir=args.review.parent, kb_path=args.review.parent / 'knowledge-base')
    if not args.execute:
        blocker = 'execution_not_requested'
    elif os.getenv('CORE_DEMO', 'false').lower() != 'false':
        blocker = 'demo_mode_forbidden'
    elif missing:
        blocker = 'missing_real_api_configuration'
    else:
        try:
            config = replace(Settings.from_environment(), data_dir=args.review.parent,
                             kb_path=args.review.parent / 'knowledge-base', demo=False)
            secret = config.api_key
            from core.engine import EnergytoolCore
            from core.llm import QwenClient
            observed = ObservedClient(QwenClient(config))
            core = EnergytoolCore(config, client=observed)
        except Exception:
            blocker = 'real_core_initialization_failed'
    selected_settings = {key: getattr(config, key) for key in ('demo', 'model', 'temperature', 'max_tokens', 'top_k', 'chunk_chars', 'context_chars', 'history_chars', 'request_seconds', 'network_seconds', 'retries', 'max_workers')}
    inputs = [args.review / name for name in ('product_facts.csv', 'cases.jsonl', 'role3-critical.jsonl', 'role3-holdout.jsonl', 'role3-live.jsonl')]
    inputs += [args.review.parent / 'knowledge-base.zip', args.review.parent / 'evaluation/demo_questions.csv']
    sources = args.review.parent / 'knowledge-base'
    source_hashes = {p.relative_to(sources).as_posix(): sha256(p)
                     for p in sorted(sources.rglob('*')) if p.is_file()}
    code_paths = [ROOT / 'consultant_core.py', ROOT / 'core_contract.py', ROOT / 'solution.py']
    code_paths += sorted((ROOT / 'core').glob('*.py'))
    passport = {'run_id': run_id, 'created_at_msk': now.isoformat(), 'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'source_revision': args.source_revision, 'runner_sha256': sha256(Path(__file__)),
                'prompt_version': PROMPT_VERSION, 'prompt_sha256': sha256(ROOT / 'core/prompts.py'),
                'inputs_sha256': {p.name: sha256(p) for p in inputs}, 'settings': selected_settings,
                'source_files_sha256': source_hashes,
                'runtime_code_sha256': {p.relative_to(ROOT).as_posix(): sha256(p) for p in code_paths},
                'settings_state': 'actual_provider_settings' if core else 'offline_plan_defaults_not_provider_observations',
                'execution_workers': 1,
                'transport': 'direct_core', 'missing_configuration_names': missing,
                'execute_requested': args.execute, 'context_fixture_enabled': args.include_context_fixtures,
                'criteria_status': args.criteria_status,
                'preliminary_criteria': {'main_success_minimum': 27, 'main_total': 30, 'critical_defects_maximum': 0},
                'history_modes': ['seeded', 'live_model_answers'],
                'raw_selection_visibility': 'observed_client_return_value_if_called; no hidden reasoning or auth headers'}
    write_json(out / 'passport.json', redact(passport, secret))
    journal = out / 'results.jsonl'
    fields = ['attempt_id', 'status', 'intent', 'facts', 'limits', 'history', 'privacy', 'usefulness', 'overall', 'reason', 'defect_id']
    try:
        def invoke(conversation, deadline):
            from core_contract import CoreRequest
            request = CoreRequest(message=conversation[-1]['content'], history=conversation[:-1])
            return core.respond(request, deadline=deadline)
        # Доказательства сохраняются после каждой попытки, а не в конце длинного прогона.
        with journal.open('x', encoding='utf-8') as stream, (out / 'assessments.csv').open('x', encoding='utf-8', newline='') as assessments:
            journal.chmod(0o600)
            (out / 'assessments.csv').chmod(0o600)
            writer = csv.DictWriter(assessments, fieldnames=fields)
            writer.writeheader()
            partial_rows = []
            def record(row):
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
                writer.writerow({'attempt_id': row['attempt_id'], 'status': row['status'], 'overall': 'blocked' if row['status'] == 'blocked' else 'not_reviewed'})
                assessments.flush()
                partial_rows.append(row)
                partial = summarize(partial_rows)
                partial.update(run_id=run_id, planned_total=len(queue), partial=True,
                               blocking_reason=blocker, model_generation_evaluated=False)
                write_json(out / 'summary.json', partial)
            rows = collect(queue, invoke=invoke if core else None, observed=observed,
                           blocker=blocker, secret=secret, allow_fixture=args.include_context_fixtures,
                           budget_seconds=args.budget_seconds, record_callback=record)
    finally:
        if core:
            core.close()
    summary = summarize(rows)
    summary.update(run_id=run_id, blocking_reason=blocker, model_generation_evaluated=False,
                   decision='generation_blocked' if blocker else 'answers_pending_human_review')
    write_json(out / 'summary.json', summary)
    print(json.dumps({'run_id': run_id, 'planned_attempts': len(rows), 'statuses': summary['statuses'],
                      'model_calls_completed': summary['model_calls_completed'], 'blocking_reason': blocker}, ensure_ascii=False))
    return 2 if blocker else 3 if summary['statuses'].get('technical_failure') else 0


if __name__ == '__main__':
    raise SystemExit(main())
