"""Проверка действий консультанта на размеченных диалогах Ани с настоящей моделью."""

import argparse
import json
from pathlib import Path
import sys
from unittest.mock import patch
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from consultant_core import create_core
from core.config import Settings
from core_contract import CoreRequest, CoreUnavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.cases.resolve():
        parser.error("Отчёт должен храниться отдельно от разметки")
    settings = Settings.from_environment()
    if settings.demo:
        parser.error("Для проверки действий нужна настоящая модель")
    core = create_core(settings)
    rows = []
    try:
        for line in args.cases.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            turns = case["conversation"]
            request = CoreRequest(message=turns[-1]["content"], history=turns[:-1])
            row = {"id": case["case_id"], "expected": case["expected_action"]}
            original_context = core.index.context

            def evaluation_context(hits, budget):
                """Проверочная строка попадает лишь в копию возвращённого контекста."""
                chunks = original_context(hits, budget)
                fixture = case.get("context_fixture")
                if chunks and fixture:
                    first = replace(chunks[0], text=chunks[0].text + "\n" + fixture["text"])
                    return (first, *chunks[1:])
                return chunks

            try:
                with patch.object(core.index, "context", evaluation_context):
                    response = core.respond(request)
                row.update(actual=response.core_status, answer=response.answer,
                           sources=[src.chunk_id for src in response.sources])
            except CoreUnavailable as exc:
                row.update(actual="technical_failure", error=getattr(exc, "category", type(exc).__name__))
            rows.append(row)
    finally:
        core.close()
    report = {"rows": len(rows), "actions_match": sum(r["actual"] == r["expected"] for r in rows),
              "technical_failures": sum(r["actual"] == "technical_failure" for r in rows),
              "cases": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print({k: v for k, v in report.items() if k != "cases"})


if __name__ == "__main__":
    main()
