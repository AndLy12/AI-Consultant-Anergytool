"""Проверяет сохранность корпуса при подготовке; закрытые материалы не нужны."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.prepare_knowledge_base import prepare


class KnowledgePreparationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.archive = self.root / "input.zip"
        self.sources = self.root / "sources"
        self.artifacts = self.root / "artifacts"

    def archive_with(self, files):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)

    def run_prepare(self):
        return prepare(self.archive, self.sources, self.artifacts)

    def test_table_and_caveat_remain_together_without_text_loss(self):
        text = "# Тест\r\n\r\n## Показатели\r\n\r\n  Значение   Условие\r\n  --------   -------\r\n  123        пример\r\n\r\nНе является гарантией.\r\n\r\n## Что мы не делаем\r\nНе обещаем результат.\r\n"
        raw = b"\xef\xbb\xbf" + text.encode("utf-8")
        self.archive_with({"product/test.md": raw})
        manifest = self.run_prepare()
        chunks = [json.loads(row) for row in (self.artifacts / "chunks.jsonl").read_text().splitlines()]
        self.assertEqual(manifest["chunk_count"], 2)
        self.assertEqual((self.sources / "product/test.md").read_bytes(), raw)
        self.assertEqual("".join(c["text"] for c in chunks), text)
        self.assertIn("123        пример", chunks[0]["text"])
        self.assertIn("Не является гарантией.", chunks[0]["text"])
        self.assertEqual(chunks[0]["related_context_chunk_ids"], [chunks[1]["chunk_id"]])

    def test_repeat_is_identical_and_document_date_is_not_inferred(self):
        self.archive_with({"test.md": "# Проверка\nУпоминание 01.01.2026 — не версия документа.\n"})
        first = self.run_prepare()
        before = {p.name: p.read_bytes() for p in self.artifacts.iterdir()}
        self.assertEqual(first, self.run_prepare())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.artifacts.iterdir()})
        doc = json.loads((self.artifacts / "documents.jsonl").read_text())
        self.assertIsNone(doc["document_date"])
        self.assertIsNone(doc["version"])

    def test_traversal_and_absolute_paths_are_rejected(self):
        for name in ("../outside.md", "/outside.md", "folder/../../outside.md", "folder\\outside.md", "C:/outside.md"):
            with self.subTest(name=name):
                self.archive_with({name: "Не извлекать"})
                with self.assertRaises(ValueError):
                    self.run_prepare()
                self.assertFalse(self.sources.exists())

    def test_duplicate_normalized_paths_are_rejected(self):
        self.archive_with({"test.md": "# Один", "./test.md": "# Другой"})
        with self.assertRaises(ValueError):
            self.run_prepare()

    def test_unknown_formats_are_not_silently_omitted(self):
        self.archive_with({"test.pdf": b"not a supported source"})
        with self.assertRaises(ValueError):
            self.run_prepare()

    def test_existing_different_source_is_preserved(self):
        self.archive_with({"test.md": "# Первая версия"})
        self.run_prepare()
        self.archive_with({"test.md": "# Вторая версия"})
        with self.assertRaises(FileExistsError):
            self.run_prepare()
        self.assertEqual((self.sources / "test.md").read_text(), "# Первая версия")

    def test_nested_output_would_pollute_the_corpus(self):
        self.archive_with({"test.md": "# Тест"})
        with self.assertRaises(ValueError):
            prepare(self.archive, self.sources, self.sources / "metadata")

    def test_audit_records_defects_without_repairing_the_source(self):
        raw = "# Проверка\nСм. `missing.md`.\nТекст \\## Неверный заголовок\n"
        self.archive_with({"test.md": raw})
        manifest = self.run_prepare()
        self.assertEqual({w["kind"] for w in manifest["warnings"]}, {"missing_reference", "escaped_heading"})
        self.assertEqual((self.sources / "test.md").read_text(), raw)


if __name__ == "__main__":
    unittest.main()
