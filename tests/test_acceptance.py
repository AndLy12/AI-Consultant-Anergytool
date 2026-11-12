"""Приёмочные проверки CLI и CSV отдельным процессом, без импорта solution."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "solution.py"


class CliContractTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.data = self.workspace / "data"
        self.data.mkdir()
        self.questions = self.data / "questions.csv"
        self.output = self.workspace / "output" / "submission.csv"
        self.write_questions([("001", "Как настроить уведомления?")])
        (self.data / "knowledge.md").write_text(
            "Уведомления настраиваются в карточке объекта.", encoding="utf-8"
        )
        # Не наследуем реальные ключи разработчика: тесты не должны обращаться к API.
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("LLM_", "OPENAI_"))
            and key not in {"DATA_DIR", "QUESTIONS_PATH", "OUTPUT_PATH", "TOP_K", "LOG_LEVEL"}
        }
        self.env.update({
            "DATA_DIR": str(self.data), "OUTPUT_PATH": str(self.output),
            "PYTHONIOENCODING": "utf-8", "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        })

    def write_questions(self, rows, columns=("id", "question"), encoding="utf-8"):
        with self.questions.open("w", encoding=encoding, newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)

    def invoke(self, *arguments, expected_code=0):
        result = subprocess.run(
            [sys.executable, str(ENTRYPOINT), *arguments], cwd=self.workspace,
            env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
        return result

    def read_result(self, ids):
        with self.output.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, ["id", "answer"])
            rows = list(reader)
        self.assertEqual([row["id"] for row in rows], ids)
        for row in rows:
            self.assertEqual(set(row), {"id", "answer"})
            self.assertTrue(row["answer"].strip())
            self.assertLessEqual(len(row["answer"]), 10_000)
        return rows

    def test_environment_only_launch_preserves_ids_and_order(self):
        self.write_questions([("001", "Уведомления"), ("x-Я", "Отчёт"), ("000", "")])
        self.invoke()
        self.read_result(["001", "x-Я", "000"])

    def test_explicit_paths_override_environment(self):
        self.env["QUESTIONS_PATH"] = "does-not-exist.csv"
        self.env["OUTPUT_PATH"] = str(self.workspace / "unused.csv")
        self.invoke("--questions", str(self.questions), "--output", str(self.output))
        self.read_result(["001"])
        self.assertFalse((self.workspace / "unused.csv").exists())

    def test_empty_question_has_clarification(self):
        self.write_questions([("empty", "   ")])
        self.invoke()
        answer = self.read_result(["empty"])[0]["answer"].lower()
        self.assertIn("вопрос", answer)

    def test_absent_knowledge_does_not_invent_answer(self):
        (self.data / "knowledge.md").unlink()
        self.invoke()
        self.assertIn("недостаточно данных", self.read_result(["001"])[0]["answer"])

    def test_unknown_topic_does_not_echo_the_question_as_knowledge(self):
        self.write_questions([("weather", "Погода на Марсе")])
        self.invoke()
        self.assertIn("недостаточно данных", self.read_result(["weather"])[0]["answer"])

    def test_supported_question_columns_and_utf8_bom(self):
        for column in ("question", "query", "text", "message"):
            with self.subTest(column=column):
                self.write_questions([("001", "Уведомления")], ("id", column), "utf-8-sig")
                self.invoke()
                self.assertIn("карточке объекта", self.read_result(["001"])[0]["answer"])

    def test_invalid_input_fails_without_creating_submission(self):
        cases = {
            "duplicate": "id,question\n1,Первый\n1,Второй\n",
            "empty_id": "id,question\n,Вопрос\n",
            "missing_id_column": "question\nВопрос\n",
            "missing_question_column": "id,other\n1,Вопрос\n",
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                self.questions.write_text(content, encoding="utf-8")
                result = self.invoke(expected_code=1)
                self.assertFalse(self.output.exists())
                self.assertNotIn("Traceback", result.stderr)

    def test_missing_input_fails_with_readable_error(self):
        self.questions.unlink()
        result = self.invoke(expected_code=1)
        self.assertIn("Questions file not found", result.stderr)
        self.assertFalse(self.output.exists())

    def test_corrupted_knowledge_file_does_not_abort_batch(self):
        (self.data / "broken.json").write_text("{broken", encoding="utf-8")
        self.invoke()
        self.assertIn("карточке объекта", self.read_result(["001"])[0]["answer"])

    def test_second_run_does_not_duplicate_output_rows(self):
        self.invoke()
        self.invoke()
        self.read_result(["001"])

    def test_header_only_input_produces_header_only_output(self):
        self.write_questions([])
        self.invoke()
        self.read_result([])


if __name__ == "__main__":
    unittest.main()
