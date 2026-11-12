from __future__ import annotations

import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import solution


class SolutionTest(unittest.TestCase):
    def test_end_to_end_writes_exact_schema_and_non_empty_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            questions_path = data_dir / "questions.csv"
            questions_path.write_text(
                'id,question\n001,"Сколько энергии показывает отчёт?"\n002,""\n',
                encoding="utf-8",
            )
            (data_dir / "knowledge.md").write_text(
                "Отчёт показывает расход энергии за выбранный период.",
                encoding="utf-8",
            )
            output_path = data_dir / "submission.csv"
            settings = solution.Settings(
                data_dir=data_dir,
                questions_path=questions_path,
                output_path=output_path,
                llm_base_url="",
                llm_api_key="",
                llm_model="",
            )

            processed = solution.run(settings)

            self.assertEqual(processed, 2)
            with output_path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, ["id", "answer"])
                rows = list(reader)
            self.assertEqual([row["id"] for row in rows], ["001", "002"])
            self.assertTrue(all(row["answer"].strip() for row in rows))

    def test_duplicate_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "questions.csv"
            path.write_text("id,question\n1,Первый\n1,Второй\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Duplicate id"):
                solution.load_questions(path)

    def test_retrieve_returns_empty_for_unrelated_question(self) -> None:
        knowledge = [solution.SearchResult("source", "Настройка уведомлений", 0.0)]
        self.assertEqual(solution.retrieve("Прогноз погоды", knowledge), [])

    def test_explicit_questions_scope_knowledge_to_input_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            questions_path = Path(temporary_directory) / "questions.csv"
            questions_path.write_text("id,question\n1,Вопрос\n", encoding="utf-8")

            with patch.dict(os.environ, {"DATA_DIR": ""}):
                settings = solution.Settings.from_environment(str(questions_path), None)

            self.assertEqual(settings.data_dir, questions_path.parent.resolve())

    def test_csv_escaping_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "submission.csv"
            answer = 'Строка с запятой, "кавычками" и\nпереносом.'
            solution.write_submission(path, [("x", answer)])
            with path.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row, {"id": "x", "answer": answer})


if __name__ == "__main__":
    unittest.main()

