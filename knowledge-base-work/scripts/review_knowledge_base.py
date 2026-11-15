"""Проверяет разметку источников и поиск нового ядра без вызовов языковой модели.

Закрытые факты, вопросы и отчёты читаются из data/, не из исходников скрипта.
Совпадение цитаты подтверждает её происхождение, но не истинность пересказа.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
from pathlib import Path
import sys
from collections import Counter


FACT_FIELDS = {
    'fact_id', 'topic', 'statement', 'conditions', 'limitations', 'document_id',
    'source_path', 'line_start', 'line_end', 'evidence', 'uncertainty',
}
ACTIONS = {'answered', 'clarification_needed', 'insufficient_data', 'out_of_scope'}


def validate_review(sources: Path, review: Path) -> tuple[list[dict], list[dict], dict]:
    """Проверяет уникальные ID, точные цитаты, координаты и связи вопросов с фактами.

    Отсутствие сведений в базе и корректность ожидаемого действия требуют
    содержательной проверки человеком; этот валидатор их не доказывает.
    """
    with (review / 'product_facts.csv').open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not FACT_FIELDS <= set(reader.fieldnames or []):
            raise ValueError('В реестре фактов не хватает обязательных колонок')
        facts = list(reader)
    if not facts:
        raise ValueError('Реестр фактов пуст')
    seen = set()
    for row in facts:
        if not row['fact_id'].strip() or row['fact_id'] in seen:
            raise ValueError('Пустой или повторяющийся fact_id')
        seen.add(row['fact_id'])
        for field in ('topic', 'statement', 'source_path', 'document_id', 'evidence'):
            if not row[field].strip():
                raise ValueError(f"{row['fact_id']}: пустое поле {field}")
        path = sources / row['source_path']
        if Path(row['source_path']).is_absolute() or not path.resolve().is_relative_to(sources.resolve()):
            raise ValueError('Источник выходит за каталог базы')
        text = path.read_bytes().decode('utf-8-sig')
        lines = text.splitlines(keepends=True)
        start, end = int(row['line_start']), int(row['line_end'])
        if not 1 <= start <= end <= len(lines):
            raise ValueError(f"{row['fact_id']}: некорректный диапазон строк")
        if row['evidence'] not in ''.join(lines[start - 1:end]):
            raise ValueError(f"{row['fact_id']}: цитата не подтверждена указанными строками")
    cases = [json.loads(line) for line in (review / 'cases.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    if not cases:
        raise ValueError('Набор вопросов пуст')
    case_ids = set()
    for case in cases:
        key = case.get('case_id')
        if not isinstance(key, str) or not key.strip() or key in case_ids:
            raise ValueError('Пустой или повторяющийся case_id')
        case_ids.add(key)
        if case.get('expected_action') not in ACTIONS:
            raise ValueError(f'{key}: неизвестное ожидаемое действие')
        if not isinstance(case.get('category'), str) or not case['category'].strip():
            raise ValueError(f'{key}: не указана категория')
        conversation = case.get('conversation')
        if not isinstance(conversation, list) or not conversation:
            raise ValueError(f'{key}: нет диалога')
        if any(not isinstance(turn, dict) or set(turn) != {'role', 'content'}
               or turn['role'] not in {'user', 'assistant'}
               or not isinstance(turn['content'], str) or not turn['content'].strip()
               for turn in conversation):
            raise ValueError(f'{key}: неверный формат реплик')
        if conversation[-1]['role'] != 'user':
            raise ValueError(f'{key}: диалог должен завершаться репликой пользователя')
        expected = case.get('expected_fact_ids')
        if not isinstance(expected, list) or any(not isinstance(key, str) or key not in seen for key in expected):
            raise ValueError(f'{key}: неизвестные факты')
        if len(expected) != len(set(expected)):
            raise ValueError(f'{key}: повтор факта')
        if case['expected_action'] == 'answered' and not expected:
            raise ValueError(f'{key}: для ответа нужны подтверждающие факты')
        forbidden = case.get('forbidden_claims')
        if not isinstance(forbidden, list) or not forbidden or any(not isinstance(v, str) or not v.strip() for v in forbidden):
            raise ValueError(f'{key}: не указаны недопустимые утверждения')
        if not isinstance(case.get('comment'), str) or not case['comment'].strip():
            raise ValueError(f'{key}: не объяснено назначение сценария')
    summary = {
        'facts': len(facts), 'cases': len(cases),
        'actions': dict(Counter(c['expected_action'] for c in cases)),
        'source_quotes_verified': True, 'semantic_correctness_automatically_verified': False,
        'facts_sha256': hashlib.sha256((review / 'product_facts.csv').read_bytes()).hexdigest(),
        'cases_sha256': hashlib.sha256((review / 'cases.jsonl').read_bytes()).hexdigest(),
    }
    return facts, cases, summary


def check_retrieval(core_root: Path, sources: Path, facts: list[dict], cases: list[dict], top_k: int) -> dict:
    """Использует настоящий загрузчик и запрос нового ядра, но не его LLM-клиент.

    Отчёт различает попадание документа и наличие всех точных оснований в
    контексте. Непрофильные/отрицательные случаи не оцениваются как Recall.
    """
    if not 1 <= top_k <= 20:
        raise ValueError('top_k должен быть от 1 до 20')
    sys.path.insert(0, str(core_root.resolve()))
    config = importlib.import_module('core.config')
    documents = importlib.import_module('core.documents')
    retrieval = importlib.import_module('core.retrieval')
    dialogue = importlib.import_module('core.dialogue')
    contract = importlib.import_module('core_contract')
    settings = config.Settings(data_dir=sources.parent, kb_path=sources, top_k=top_k)
    chunks = documents.load_chunks(settings)
    index = retrieval.SearchIndex(chunks)
    source_ids = {}
    for name in {f['source_path'] for f in facts}:
        blocks = documents.read_blocks(name, (sources / name).read_bytes())
        own = documents.make_chunks(name, blocks, settings.chunk_chars)
        source_ids[name] = own[0].document_id
    facts_by_id = {f['fact_id']: f for f in facts}
    mapped = {}
    for fact in facts:
        quote = documents.clean_text(fact['evidence'])
        doc_id = source_ids[fact['source_path']]
        mapped[fact['fact_id']] = [c.chunk_id for c in chunks if c.document_id == doc_id and quote in c.text]
    rows = []
    for case in cases:
        request = contract.CoreRequest(message=case['conversation'][-1]['content'], history=case['conversation'][:-1])
        history, _ = dialogue.bounded_history(request, settings.history_chars)
        bounded = contract.CoreRequest(message=request.message, history=history)
        hits = index.search(dialogue.search_query(bounded), top_k)
        context = index.context(hits, settings.context_chars)
        expected = case['expected_fact_ids']
        expected_docs = {source_ids[facts_by_id[key]['source_path']] for key in expected}
        context_ids = {c.chunk_id for c in context}
        covered = [key for key in expected if context_ids.intersection(mapped[key])]
        rows.append({
            'case_id': case['case_id'],
            # Сценарий с подменённым контекстом требует отдельного ручного прогона.
            'scored': case['expected_action'] == 'answered' and not case.get('context_fixture'),
            'document_hit_at_k': bool(expected_docs.intersection(h.chunk.document_id for h in hits)),
            'expected_fact_ids': expected, 'facts_with_exact_evidence_in_context': covered,
            'unmapped_fact_ids': [key for key in expected if not mapped[key]],
            'top_chunk_ids': [h.chunk.chunk_id for h in hits],
            'context_chunk_ids': [c.chunk_id for c in context],
            'top_result_is_formatting_only': bool(hits) and not any(ch.isalnum() for ch in hits[0].chunk.text),
            'context_fixture_pending': bool(case.get('context_fixture')),
        })
    scored = [row for row in rows if row['scored']]
    return {
        'chunk_count': len(chunks), 'document_count': len({c.document_id for c in chunks}),
        # Старые снимки ядра ранжируют фрагменты; различие важно для сравнения top-k.
        'search_unit': 'section' if hasattr(index, 'sections') else 'chunk',
        'search_sections': len(index.sections) if hasattr(index, 'sections') else None,
        'top_k': top_k, 'chunk_chars': settings.chunk_chars, 'context_chars': settings.context_chars,
        'formatting_only_chunks': sum(not any(ch.isalnum() for ch in c.text) for c in chunks),
        'cases_with_formatting_only_top_result': sum(row['top_result_is_formatting_only'] for row in rows),
        'context_fixture_cases_pending': sum(row['context_fixture_pending'] for row in rows),
        'answered_cases_scored': len(scored),
        'document_hit_cases': sum(row['document_hit_at_k'] for row in scored),
        'all_required_evidence_cases': sum(set(row['expected_fact_ids']) <= set(row['facts_with_exact_evidence_in_context']) for row in scored),
        'unmapped_fact_ids': sorted(key for key, ids in mapped.items() if not ids),
        'fact_to_core_chunk_ids': mapped, 'cases': rows,
        'llm_called': False, 'generated_answers_evaluated': False,
        'metric_note': 'Проверка точных размеченных оснований, а не семантическая оценка ответа; альтернативный подтверждающий источник может не совпасть с выбранной цитатой. Пропуски требуют ручного разбора.',
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='Проверка закрытой разметки и совместимости с ядром без LLM')
    parser.add_argument('--sources', type=Path, default=Path('data/knowledge-base'))
    parser.add_argument('--review', type=Path, default=Path('data/review'))
    parser.add_argument('--output', type=Path, default=Path('data/review/validation.json'))
    parser.add_argument('--core-root', type=Path)
    parser.add_argument('--core-revision', default='не указана')
    parser.add_argument('--top-k', type=int, default=5)
    args = parser.parse_args()
    facts, cases, summary = validate_review(args.sources, args.review)
    result = {'annotations': summary}
    if args.core_root:
        result['core_revision'] = args.core_revision
        result['retrieval'] = check_retrieval(args.core_root, args.sources, facts, cases, args.top_k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Не выводим закрытые вопросы, цитаты, пути к API или ответы в общий лог.
    print(f"Проверено фактов: {summary['facts']}; сценариев: {summary['cases']}; модель не вызывалась.")
    if 'retrieval' in result:
        metrics = result['retrieval']
        print(f"Документ в top-{args.top_k}: {metrics['document_hit_cases']}/{metrics['answered_cases_scored']}; все основания в контексте: {metrics['all_required_evidence_cases']}/{metrics['answered_cases_scored']}.")


if __name__ == '__main__':
    main()
