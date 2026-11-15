"""Независимый QA: фиксация закрытого набора, аудит и offline-поиск без LLM.

Набор и критерии фиксируются до генерации; готовые ответы сюда не подставляются.
В Git допускается этот инструмент и синтетические тесты, но не его рабочие данные.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import io
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
VERSION = "role3-quality-v1"
SEVERITIES = {"critical", "substantial", "minor"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def normalized(text):
    return " ".join(text.split())


def excluded_required(hit, products):
    """Учитываем и продуктовый заголовок, и закреплённый список подготовки.

    У списка подготовки title общий для документа, поэтому проверка только title
    пропустила бы ошибочный required-раздел. Это метка разметчика, не классификатор намерения.
    """
    aliases = {"солнечные электростанции": ("солнечные электростанции", "для солнечной станции"),
               "облачная платформа": ("облачная платформа", "для платформы учёта"),
               "выход на оптовый рынок": ("выход на оптовый рынок", "выхода на оптовый рынок")}
    label = (hit.chunk.title + "\n" + (hit.chunk.section or "")).lower().replace("ё", "е")
    return hit.required and any(alias.lower().replace("ё", "е") in label
                                for product in products for alias in aliases.get(product, (product,)))


def private_path(path):
    """Рабочие данные внутри checkout должны быть исключены из Git."""
    path = Path(path).expanduser().resolve()
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        return path
    checked = subprocess.run(["git", "check-ignore", "--quiet", "--", str(relative)], cwd=ROOT)
    if checked.returncode != 0:
        raise ValueError("Рабочие данные внутри репозитория должны находиться в игнорируемой папке")
    return path


def save(path, value):
    """Не перезаписываем артефакты; отдельный прогон получает новый путь."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(value) + b"\n")


def corpus_documents(path):
    """Проверка цитат для Markdown-корпуса: ZIP читаем без распаковки."""
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise ValueError("Для фиксации источников передайте ZIP с Markdown-документами")
    with zipfile.ZipFile(path) as archive:
        info = archive.infolist()
        if sum(item.file_size for item in info) > 256 * 1024 * 1024:
            raise ValueError("Слишком большой корпус")
        documents = {}
        for item in info:
            if item.filename.endswith(".md"):
                if item.file_size > 32 * 1024 * 1024 or item.filename in documents:
                    raise ValueError("Некорректный документ корпуса")
                documents[item.filename] = archive.read(item).decode("utf-8-sig")
    if not documents:
        raise ValueError("В корпусе нет Markdown-документов")
    return documents


def section_text(text, section):
    """Цитата должна находиться именно в указанном разделе, включая подразделы."""
    headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text))
    for index, heading in enumerate(headings):
        if heading[2].strip() == section:
            end = next((other.start() for other in headings[index + 1:] if len(other[1]) <= len(heading[1])), len(text))
            return text[heading.end():end]
    raise ValueError("Указанный раздел источника не найден")


def read_cases(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_cases(cases, documents):
    """Проверяем идентичность входов и наличие критериев для каждого user-хода.

    Совпадение цитаты подтверждает происхождение, но не семантическую применимость.
    Последняя остаётся отдельной проверкой Ани/независимого разметчика.
    """
    ids, fingerprints = set(), set()
    for case in cases:
        identity = case["case_id"]
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError("Пустой или повторный case_id")
        ids.add(identity)
        for field in ("category", "independence_group", "intent"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError("Не заполнены категория, группа независимости или намерение")
        if case.get("split") not in {"development", "final"}:
            raise ValueError("Нужен явный development/final")
        mode = case.get("evaluation_mode")
        if mode not in {"single", "fixed_transcript", "live_chain"}:
            raise ValueError("Неизвестный режим сценария")
        conversation = case["conversation"]
        if not conversation or conversation[-1].get("role") != "user":
            raise ValueError("Сценарий должен заканчиваться репликой пользователя")
        for turn in conversation:
            if set(turn) != {"role", "content"} or turn["role"] not in {"user", "assistant"}:
                raise ValueError("Неизвестные поля/роль в диалоге")
            if not isinstance(turn["content"], str) or not turn["content"].strip():
                raise ValueError("Пустой ход")
        if mode == "single" and len(conversation) != 1:
            raise ValueError("Одиночный сценарий должен содержать один ход")
        if mode == "live_chain" and (not 2 <= len(conversation) <= 6 or any(t["role"] != "user" for t in conversation)):
            raise ValueError("Живая цепочка содержит 2–6 будущих user-реплик, без выдуманных ответов")
        fingerprint = digest(canonical(conversation))
        if fingerprint in fingerprints:
            raise ValueError("Один вход повторён под разными ID")
        fingerprints.add(fingerprint)
        user_turns = {i + 1 for i, turn in enumerate(conversation) if turn["role"] == "user"}
        criteria = case["criteria"]
        if len(criteria) != len(user_turns) or {rule["turn"] for rule in criteria} != user_turns:
            raise ValueError("Для каждого пользовательского хода нужен свой критерий")
        for rule in criteria:
            if rule.get("severity") not in SEVERITIES:
                raise ValueError("Не определена серьёзность")
            for field in ("required_facts", "forbidden_claims", "acceptable_alternatives"):
                values = rule.get(field)
                if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
                    raise ValueError("До генерации нужны факты, запреты и допустимые альтернативы")
            if not rule.get("sources"):
                raise ValueError("Критерии без источников не готовы к фиксации")
            for source in rule["sources"]:
                text = documents.get(source["document"])
                if text is None or not isinstance(source.get("quote"), str) or len(source["quote"].strip()) < 8:
                    raise ValueError("Источник или точная цитата не найдены")
                if normalized(source["quote"]) not in normalized(section_text(text, source["section"])):
                    raise ValueError("Цитата не принадлежит указанному разделу")
    # Пара из developer/final не является независимой проверкой переноса.
    groups = {}
    for case in cases:
        group = case["independence_group"]
        if group in groups and groups[group] != case["split"]:
            raise ValueError("Связанные сценарии нельзя разделять между development и final")
        groups[group] = case["split"]


def freeze(source, corpus, destination, revision):
    """Снимок создаётся один раз, до генерации; роли/версии ответов в него не входят."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Укажите полный SHA исходной версии")
    source = Path(source)
    cases = read_cases(source)
    validate_cases(cases, corpus_documents(corpus))
    if not 60 <= len(cases) <= 100 or not 60 <= len({c["independence_group"] for c in cases}) <= 100:
        raise ValueError("Нужны 60–100 сценариев и не менее 60 независимых групп")
    if sum(c["split"] == "final" for c in cases) != 20:
        raise ValueError("Зафиксируйте 20 финальных сценариев")
    destination = private_path(destination)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        files = {}
        for split in ("development", "final"):
            data = b"".join(canonical(c) + b"\n" for c in cases if c["split"] == split)
            path = destination / (split + ".jsonl")
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            files[path.name] = digest(data)
        manifest = {"version": VERSION, "frozen_at": datetime.now(timezone.utc).isoformat(),
                    "source_revision": revision, "input_sha256": digest(source.read_bytes()),
                    "corpus_sha256": digest(Path(corpus).read_bytes()), "files": files,
                    "scenarios": len(cases), "independent_groups": len({c["independence_group"] for c in cases}),
                    "categories": dict(Counter(c["category"] for c in cases)),
                    "partitions": dict(Counter(c["split"] for c in cases)),
                    "semantic_alignment": "pending_independent_source_review", "model_calls": 0}
        save(destination / "manifest.json", manifest)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return {**manifest, "manifest_sha256": digest((destination / "manifest.json").read_bytes())}


def audit(bundle, expected_manifest=None):
    """Проверяем сохранённый снимок; изменение manifest фиксируют внешним SHA."""
    bundle = Path(bundle)
    manifest_data = (bundle / "manifest.json").read_bytes()
    if expected_manifest and digest(manifest_data) != expected_manifest:
        raise ValueError("Паспорт изменился относительно внешней фиксации")
    manifest = json.loads(manifest_data)
    if set(manifest["files"]) != {"development.jsonl", "final.jsonl"}:
        raise ValueError("Некорректный состав снимка")
    for name, expected in manifest["files"].items():
        if digest((bundle / name).read_bytes()) != expected:
            raise ValueError("Набор изменился после фиксации")
    return {**manifest, "manifest_sha256": digest(manifest_data)}


def export_development(bundle, output, expected_manifest):
    """Отдаём разработчику только developer-часть, без финальных вопросов/критериев.

    CSV содержит одиночные и фиксированные входы. Живые цепочки передаются
    отдельно: заранее придуманная история не подменяет фактические ответы.
    """
    manifest = audit(bundle, expected_manifest)
    cases = read_cases(Path(bundle) / "development.jsonl")
    output = private_path(output)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        files = {}
        payloads = {"development.jsonl": b"".join(canonical(c) + b"\n" for c in cases),
                    "live-chains.jsonl": b"".join(canonical(c) + b"\n" for c in cases if c["evaluation_mode"] == "live_chain")}
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["id", "question"])
        for case in cases:
            if case["evaluation_mode"] != "live_chain":
                conversation = case["conversation"]
                question = conversation[0]["content"] if len(conversation) == 1 else json.dumps(conversation, ensure_ascii=False)
                writer.writerow([case["case_id"], question])
        payloads["questions.csv"] = stream.getvalue().encode("utf-8")
        for name, data in payloads.items():
            descriptor = os.open(output / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(data)
            files[name] = digest(data)
        passport = {"kind": "development_handoff", "baseline_revision": manifest["source_revision"],
                    "frozen_manifest_sha256": manifest["manifest_sha256"], "corpus_sha256": manifest["corpus_sha256"],
                    "scenarios": len(cases), "live_chains": sum(c["evaluation_mode"] == "live_chain" for c in cases),
                    "files": files, "final_included": False, "model_calls": 0,
                    "source_alignment": "pending_independent_source_review"}
        save(output / "passport.json", passport)
    except BaseException:
        shutil.rmtree(output)
        raise
    return {"status": "prepared", "scenarios": len(cases), "live_chains": passport["live_chains"],
            "final_included": False, "model_calls": 0, "passport_sha256": digest((output / "passport.json").read_bytes())}


def wilson(passed, total):
    """95% Wilson для доли; это не оценка балла платформы."""
    if not isinstance(total, int) or not isinstance(passed, int) or total <= 0 or not 0 <= passed <= total:
        raise ValueError("Некорректные счётчики")
    z = 1.959963984540054
    p = passed / total
    scale = 1 + z*z / total
    center = (p + z*z / (2*total)) / scale
    half = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / scale
    return [max(0, center-half), min(1, center+half)]


def search_report(bundle, data_dir, output):
    """Проверяем только developer-часть. Финал и генератор здесь недоступны."""
    from core.config import Settings
    from core.documents import load_chunks
    from core.retrieval import SearchIndex
    from core_contract import CoreRequest
    manifest = audit(bundle)
    corpus = Path(data_dir) / "knowledge-base.zip"
    if digest(corpus.read_bytes()) != manifest["corpus_sha256"]:
        raise ValueError("Корпус отличается от зафиксированного")
    settings = Settings(data_dir=Path(data_dir), kb_path=corpus)
    chunks = load_chunks(settings)
    index = SearchIndex(chunks)
    records = []
    for case in read_cases(Path(bundle) / "development.jsonl"):
        if case["evaluation_mode"] == "live_chain":
            records.append({"case_id": case["case_id"], "status": "needs_real_previous_answers"})
            continue
        request = CoreRequest(message=case["conversation"][-1]["content"], history=case["conversation"][:-1])
        for top_k in (3, 5, 8):
            hits = index.search_request(request, top_k)
            context = index.context(hits, settings.context_chars)
            joined = normalized("\n".join(c.text for c in context))
            criteria = case["criteria"][-1]
            evidence = criteria["sources"]
            # Это покрытие заданных цитат, не доказательство релевантности или полноты ответа.
            covered = [normalized(source["quote"]) in joined for source in evidence]
            excluded = [h.chunk.chunk_id for h in hits if excluded_required(h, case.get("excluded_products", []))]
            records.append({"case_id": case["case_id"], "top_k": top_k, "context_quote_coverage": covered,
                            "excluded_required": excluded, "hits": [h.chunk.chunk_id for h in hits],
                            "context_chars": sum(len(c.text) for c in context), "semantic_verdict": "not_reviewed"})
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    code_files = [path for path in (ROOT / "core").glob("*.py")]
    code_files += [ROOT / name for name in ("core_contract.py", "consultant_core.py", "solution.py")]
    code_hash = digest(canonical({str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(code_files)}))
    report = {"kind": "offline_retrieval", "baseline_revision": manifest["source_revision"],
              "checkout_revision": checkout, "core_files_sha256": code_hash,
              "manifest_sha256": manifest["manifest_sha256"], "corpus_sha256": manifest["corpus_sha256"],
              "model_calls": 0, "final_used": False, "records": records}
    save(private_path(output), report)
    return {"status": "completed", "kind": report["kind"], "model_calls": 0,
            "records": len(records), "final_used": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("freeze")
    command.add_argument("--cases", type=Path, required=True)
    command.add_argument("--corpus", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--source-revision", required=True)
    command = commands.add_parser("audit")
    command.add_argument("--bundle", type=Path, required=True)
    command.add_argument("--expected-manifest-sha256")
    command = commands.add_parser("search")
    command.add_argument("--bundle", type=Path, required=True)
    command.add_argument("--data-dir", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("export-development")
    command.add_argument("--bundle", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            report = freeze(args.cases, args.corpus, args.output, args.source_revision)
        elif args.command == "audit":
            report = audit(args.bundle, args.expected_manifest_sha256)
        elif args.command == "export-development":
            report = export_development(args.bundle, args.output, args.expected_manifest_sha256)
        else:
            report = search_report(args.bundle, args.data_dir, args.output)
        # Ни вопросы, ни цитаты в публичный stdout не выводятся.
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile):
        print(json.dumps({"status": "invalid_or_incomplete", "model_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
