"""Проверяет настоящую базу и поиск без API, веба и использования demo-ответов.

Подробный отчёт содержит только ID и параметры поиска, но всё равно хранится
локально. Наличие контекста не доказывает полезность будущего ответа модели.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import Settings
from core.dialogue import bounded_history, parse_question
from core.documents import load_chunks
from core.retrieval import SearchIndex
from core_contract import CoreRequest
from solution import load_questions


def check(data_dir: Path, questions: Path | None = None, *, top_k: int = 5) -> dict:
    """Одинаковый поиск с respond; секреты и настройки провайдера не требуются."""
    if not 1 <= top_k <= 20:
        raise ValueError('top_k должен быть от 1 до 20')
    started = time.monotonic()
    settings = Settings(data_dir=data_dir, top_k=top_k)
    chunks = load_chunks(settings)
    index = SearchIndex(chunks)
    rows = []
    for question in load_questions(questions) if questions else []:
        request = parse_question(question.text)
        history, truncated = bounded_history(request, settings.history_chars)
        request = CoreRequest(message=request.message, history=history)
        hits = index.search_request(request, settings.top_k)
        context = index.context(hits, settings.context_chars)
        rows.append({
            'id': question.id, 'history_turns': len(history), 'history_truncated': truncated,
            'top_chunk_ids': [hit.chunk.chunk_id for hit in hits],
            'context_chunk_ids': [c.chunk_id for c in context],
            'context_chars': sum(len(c.text) for c in context),
        })
    return {
        'documents': len({c.document_id for c in chunks}), 'chunks': len(chunks),
        'search_unit': 'section', 'search_sections': len(index.sections),
        'formatting_only_chunks': sum(not any(ch.isalnum() for ch in c.text) for c in chunks),
        'top_k': top_k, 'chunk_chars': settings.chunk_chars,
        'context_chars': settings.context_chars, 'questions': len(rows),
        'dialogue_questions': sum(row['history_turns'] > 0 for row in rows),
        'questions_with_context': sum(bool(row['context_chunk_ids']) for row in rows),
        'questions_sha256': hashlib.sha256(questions.read_bytes()).hexdigest() if questions else None,
        'llm_called': False, 'generated_answers_evaluated': False,
        'elapsed_seconds': round(time.monotonic() - started, 3), 'cases': rows,
    }


def main():
    """В общий лог попадают только агрегаты, без закрытых вопросов и документов."""
    parser = argparse.ArgumentParser(description='Проверить базу и поиск ядра без LLM')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--questions', type=Path)
    parser.add_argument('--top-k', type=int, default=5)
    parser.add_argument('--output', type=Path, default=Path('work/core-knowledge-check.json'))
    args = parser.parse_args()
    # Отчёт не должен попасть в индекс или затереть источник.
    output = args.output.resolve()
    sources = (args.data_dir / 'knowledge-base').resolve()
    if output.is_relative_to(sources) or output == (args.data_dir / 'knowledge-base.zip').resolve():
        parser.error('Отчёт должен храниться отдельно от корпуса')
    if args.questions and output == args.questions.resolve():
        parser.error('Нельзя перезаписывать исходные вопросы')
    report = check(args.data_dir, args.questions, top_k=args.top_k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
