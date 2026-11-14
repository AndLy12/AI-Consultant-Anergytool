"""Готовит локальный корпус и реестр из архива организаторов без вызовов модели.

Исходники и производные данные хранятся раздельно: текущий CSV-baseline
рекурсивно читает DATA_DIR и не должен индексировать реестр или demo-ответы.
Модуль не меняет solution.py и не подключает корпус к веб-консультанту.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def json_lines(rows: list[dict]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")


def split_sections(document: dict) -> list[dict]:
    """Сохраняет весь текст; разделы второго уровня не режутся по числу символов.

    Внутри раздела остаются таблицы, вложенные подзаголовки и оговорки.
    Это подготовка корпуса, а не оценка достаточности контекста для ответа.
    """
    lines = document["text"].splitlines(keepends=True)
    headings = [i for i, line in enumerate(lines) if line.startswith("## ")]
    if headings:
        prefix = "".join(lines[:headings[0]])
        if not re.sub(r"(?m)^# [^\r\n]*", "", prefix).strip():
            headings = headings[1:]
    boundaries = sorted({0, *headings, len(lines)})
    chunks = []
    for start, end in zip(boundaries, boundaries[1:]):
        text = "".join(lines[start:end])
        heading = re.search(r"(?m)^## ([^\r\n]+)", text)
        section = heading.group(1).strip() if heading else document["title"]
        chunk_hash = digest(text.encode("utf-8"))
        chunks.append({
            "document_id": document["document_id"],
            "chunk_id": f"{document['document_id']}:{start + 1}:{chunk_hash[:12]}",
            "title": document["title"],
            "section": section,
            "source_path": document["source_path"],
            "line_start": start + 1,
            "line_end": end,
            "page": None,
            "text": text,
            "sha256": chunk_hash,
        })
    # Указатели помогают ядру добавить ограничения вместе с найденным фрагментом.
    scope = re.compile(r"^(Кому подходит|Общие границы|Что мы не делаем|Чего утверждать нельзя|Важное ограничение)")
    context = [c["chunk_id"] for c in chunks if scope.search(c["section"])]
    for chunk in chunks:
        chunk["related_context_chunk_ids"] = [key for key in context if key != chunk["chunk_id"]]
    return chunks


def inspect_sources(documents: list[dict]) -> list[dict]:
    """Отмечает структурные дефекты без переписывания предоставленных фактов."""
    names = {d["source_path"] for d in documents}
    warnings = []
    for doc in documents:
        for number, line in enumerate(doc["text"].splitlines(), 1):
            if re.search(r"\\#{1,6}\s", line):
                warnings.append({"kind": "escaped_heading", "source_path": doc["source_path"], "line": number})
            links = re.findall(r"`([^`\n]+\.md)`|\]\(([^)\s]+\.md)(?:#[^)]*)?\)", line)
            for pair in links:
                target = next(value for value in pair if value)
                if "://" in target:
                    continue
                # В архиве встречаются ссылки относительно файла и просто имена.
                parts = list(PurePosixPath(doc["source_path"]).parent.parts)
                for part in PurePosixPath(target).parts:
                    if part == "..":
                        if parts:
                            parts.pop()
                    elif part != ".":
                        parts.append(part)
                resolved = "/".join(parts)
                if resolved not in names:
                    warnings.append({"kind": "missing_reference", "source_path": doc["source_path"], "line": number, "target": target})
    return warnings


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    """Повторный запуск допускается с теми же данными; чужие файлы не затираются."""
    for name, content in files.items():
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Выходной путь выходит за каталог результата")
        if path.exists() and path.read_bytes() != content:
            raise FileExistsError(f"Файл уже содержит другие данные: {path}; выберите новый каталог")
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)


def prepare(archive_path: Path, source_dir: Path, artifacts_dir: Path) -> dict:
    """Проверяет архив, сохраняет исходные байты и строит воспроизводимые JSONL."""
    if source_dir.resolve().is_relative_to(artifacts_dir.resolve()) or artifacts_dir.resolve().is_relative_to(source_dir.resolve()):
        raise ValueError("Исходники и служебные файлы должны лежать в разных каталогах")
    originals: dict[str, bytes] = {}
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            # При чтении ZIP Windows нормализует обратный слеш в filename;
            # orig_filename сохраняет исходное имя для одинаковой проверки на всех ОС.
            original_name = member.orig_filename
            path = PurePosixPath(original_name)
            if path.is_absolute() or ".." in path.parts or "\\" in original_name or ":" in original_name:
                raise ValueError("Недопустимый путь в архиве")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Символические ссылки в архиве не поддерживаются")
            if member.is_dir() or "__MACOSX" in path.parts:
                continue
            if path.suffix.lower() not in {".md", ".txt"}:
                raise ValueError(f"Неподдерживаемый формат источника: {path.suffix}")
            name = path.as_posix()
            if name in originals:
                raise ValueError("Повторяющийся путь документа в архиве")
            originals[name] = archive.read(member)
    if not originals:
        raise ValueError("В архиве нет документов")
    # Не допускаем незаметного смешения прежнего корпуса с новой версией.
    if source_dir.exists():
        extra = {p.relative_to(source_dir).as_posix() for p in source_dir.rglob("*") if p.is_file()} - originals.keys()
        if extra:
            raise FileExistsError("В каталоге источников есть посторонние файлы; выберите новый каталог")
    documents, chunks = [], []
    for name, raw in sorted(originals.items()):
        text = raw.decode("utf-8-sig")
        if not text.strip():
            raise ValueError(f"Пустой документ: {name}")
        title = re.search(r"(?m)^# ([^\r\n]+)", text)
        doc = {
            "document_id": str(PurePosixPath(name).with_suffix("")),
            "source_path": name,
            "title": title.group(1).strip() if title else PurePosixPath(name).stem,
            "format": PurePosixPath(name).suffix.lstrip("."),
            "bytes": len(raw), "sha256": digest(raw),
            # Упоминания дат внутри текста не считаем датой/версией документа.
            "document_date": None, "version": None, "text": text,
        }
        parts = split_sections(doc)
        doc["chunk_count"] = len(parts)
        documents.append(doc)
        chunks.extend(parts)
    manifest = {
        "schema_version": 1,
        "chunking": "markdown-h2-lossless-v1",
        "archive_sha256": digest(archive_path.read_bytes()),
        "document_count": len(documents), "chunk_count": len(chunks),
        "source_bytes": sum(len(raw) for raw in originals.values()),
        "max_chunk_chars": max(len(c["text"]) for c in chunks),
        "corpus_sha256": digest(json_bytes([[d["source_path"], d["sha256"]] for d in documents])),
        "warnings": inspect_sources(documents),
    }
    registry = io.StringIO(newline="")
    fields = [key for key in documents[0] if key != "text"]
    writer = csv.DictWriter(registry, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(documents)
    _write_files(source_dir, originals)
    _write_files(artifacts_dir, {
        "documents.jsonl": json_lines(documents), "chunks.jsonl": json_lines(chunks),
        "manifest.json": json_bytes(manifest), "registry.csv": registry.getvalue().encode("utf-8"),
    })
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Подготовка закрытой базы Energytool без модели")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--sources", type=Path, default=Path("data/knowledge-base"))
    parser.add_argument("--artifacts", type=Path, default=Path("data/kb-artifacts"))
    args = parser.parse_args()
    result = prepare(args.archive, args.sources, args.artifacts)
    print(f"Документов: {result['document_count']}; фрагментов: {result['chunk_count']}; замечаний: {len(result['warnings'])}")


if __name__ == "__main__":
    main()
