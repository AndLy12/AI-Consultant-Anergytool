"""Независимый QA: фиксация набора, offline-поиск и аудит сохранённых ответов.

Набор и критерии фиксируются до генерации; готовые ответы аудируются отдельно.
Инструмент не генерирует и не подменяет ответы модели.
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
import secrets
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


def packet_file(root, name):
    """Паспорту не разрешаем читать за пределами переданного пакета."""
    if not isinstance(name, str) or "\\" in name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("Небезопасный путь в паспорте")
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Файл паспорта отсутствует или выходит за пределы пакета")
    return path


def packet_csv(path, field):
    """ID сохраняем строками: 001 не превращается в 1."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["id", field]:
            raise ValueError("Неверные колонки CSV пакета")
        rows = list(reader)
    if not rows or any(not row["id"] or not row[field] or not row[field].strip() for row in rows):
        raise ValueError("Пустой ID или текст в CSV")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Повторные ID в CSV")
    return rows


def runtime_hash_match(blob, recorded):
    """Отличаем побайтовое совпадение от эквивалентных Windows-переносов.

    CRLF не переписывает исходник и не оправдывает произвольную разницу кода.
    SHA самого архива при этом всегда сверяется без нормализации.
    """
    if digest(blob) == recorded:
        return "exact"
    if b"\r\n" not in blob and digest(blob.replace(b"\n", b"\r\n")) == recorded:
        return "git_lf_runtime_crlf"
    return "mismatch"


def blind_handoff(packet, output, key_file):
    """Два порядка A/B для независимого проверяющего; ключ хранится отдельно.

    Маскировка названий не доказывает отсутствие прежнего знакомства с ответами.
    Разметка не заполняется автоматически и не превращается в оценку площадки.
    """
    root = Path(packet).resolve()
    passport = json.loads(packet_file(root, "passport.json").read_text())
    datasets = passport["datasets"][:2]
    if len(datasets) != 2:
        raise ValueError("Нужны два сопоставимых набора")
    folders = [packet_file(root, d["folder"] + "/passport.json").parent for d in datasets]
    questions_data = (folders[0] / "questions.csv").read_bytes()
    if questions_data != (folders[1] / "questions.csv").read_bytes():
        raise ValueError("Входы A/B отличаются")
    answers = [packet_csv(packet_file(folder, d["answers"]), "answer") for folder, d in zip(folders, datasets)]
    questions = packet_csv(folders[0] / "questions.csv", "question")
    ids = [q["id"] for q in questions]
    if any([a["id"] for a in rows] != ids for rows in answers):
        raise ValueError("ID A/B отличаются")
    sources = [{s["id"]: s["sources"] for s in read_cases(folder / "sources.jsonl")
                if s.get("channel", "single") in {"single", "csv"}} for folder in folders]
    output, key_file = private_path(output), private_path(key_file)
    if key_file.is_relative_to(output) or key_file.exists():
        raise ValueError("Ключ должен быть новым файлом вне передаваемой папки")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        files, key = {}, {"order-1": {}, "order-2": {}}

        def put(relative, data):
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            files[relative] = digest(data)

        put("questions.csv", questions_data)
        first = {identity: secrets.randbits(1) for identity in ids}
        for order_index, order in enumerate(("order-1", "order-2")):
            source_records = []
            for variant_index, variant in enumerate(("A", "B")):
                stream = io.StringIO(newline="")
                writer = csv.writer(stream)
                writer.writerow(["id", "answer"])
                for position, identity in enumerate(ids):
                    selected = first[identity] ^ order_index ^ variant_index
                    writer.writerow([identity, answers[selected][position]["answer"]])
                    key[order].setdefault(identity, {})[variant] = datasets[selected]["folder"]
                    source_records.append({"id": identity, "variant": variant, "sources": sources[selected][identity]})
                put(order + "/answers-" + variant + ".csv", stream.getvalue().encode())
            put(order + "/sources.jsonl", b"".join(canonical(row) + b"\n" for row in source_records))
        put("assessments.csv", b"id,order,reviewer,winner,severity,claim,source,reason\r\n")
        rubric = ("Проверить прямоту, факты/условия по источнику, историю, приватность и полезность. "
                  "Не требовать дословного совпадения, не штрафовать за длину саму по себе. "
                  "winner: A, B, tie либо insufficient_evidence. Ошибка/нехватка доказательств не является поражением. "
                  "Каждое спорное утверждение связать с точным источником. Это просмотр сохранённых ответов, не новый прогон.")
        public_passport = {"kind": "version_labels_hidden", "rows": len(ids), "files": files,
                           "rubric": rubric, "rubric_sha256": digest(rubric.encode()),
                           "corpus_sha256": passport["corpus_sha256"], "reviewer_prior_exposure": "unknown",
                           "new_model_calls": 0, "final_included": False, "decision": "pending_independent_review"}
        save(output / "passport.json", public_passport)
        save(key_file, {"mapping": key, "packet_manifest_sha256": digest((root / "SHA256SUMS.json").read_bytes()),
                        "blinded_passport_sha256": digest((output / "passport.json").read_bytes())})
    except BaseException:
        shutil.rmtree(output)
        raise
    return {"status": "prepared", "rows": len(ids), "orders": 2, "key_included": False,
            "new_model_calls": 0, "blinded_passport_sha256": digest((output / "passport.json").read_bytes())}


def audit_handoff(packet, corpus, output):
    """Независимый аудит сохранённых результатов; ничего из пакета не исполняем.

    Хеш доказывает целостность передачи, не истинность заявления о запуске модели.
    Цитаты сопоставляются с неизменным корпусом по ID; нового поиска/генерации нет.
    """
    from core.config import Settings
    from core.dialogue import parse_question
    from core.documents import load_chunks
    root = Path(packet).resolve()
    if any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("Ссылки в пакете не допускаются")
    expected = json.loads(packet_file(root, "SHA256SUMS.json").read_text())
    actual = {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in root.rglob("*")
              if p.is_file() and p != root / "SHA256SUMS.json"}
    if actual != expected:
        raise ValueError("Состав или хеши пакета не совпадают")
    passport = json.loads(packet_file(root, "passport.json").read_text())
    corpus = Path(corpus).resolve()
    corpus_hash = digest(corpus.read_bytes())
    if passport["corpus_sha256"] != corpus_hash:
        raise ValueError("Корпус не совпадает с паспортом пакета")
    reports, chunk_cache = [], {}
    for dataset in passport["datasets"]:
        folder_name = dataset["folder"]
        # Проверка folder через реально существующий паспорт также защищает от ../.
        folder = packet_file(root, folder_name + "/passport.json").parent
        meta = json.loads((folder / "passport.json").read_text())
        for name, wanted in meta["files_sha256"].items():
            if digest(packet_file(folder, name).read_bytes()) != wanted:
                raise ValueError("Хеш вложенного паспорта не совпадает")
        if meta["corpus_sha256"] != corpus_hash:
            raise ValueError("Корпус отдельного набора отличается")
        questions = packet_csv(folder / "questions.csv", "question")
        answers = packet_csv(packet_file(folder, dataset["answers"]), "answer")
        ids = [q["id"] for q in questions]
        if ids != [a["id"] for a in answers] or len(ids) != dataset["rows"] or len(ids) != meta["rows"]:
            raise ValueError("Наборы вопросов/ответов не совпадают")
        if digest((folder / "questions.csv").read_bytes()) != meta["questions_sha256"]:
            raise ValueError("Хеш вопросов не совпадает")
        results = read_cases(folder / "results.jsonl")
        row_id = lambda row: row.get("id", row.get("case", {}).get("case_id"))
        if [row_id(row) for row in results] != ids:
            raise ValueError("ID журнала не совпадают с CSV")
        sources = read_cases(folder / "sources.jsonl")
        source_keys = [(s["id"], s["turn"], s.get("channel", "single")) for s in sources]
        if len(source_keys) != len(set(source_keys)) or {s["id"] for s in sources} != set(ids):
            raise ValueError("Потеряны или повторены источники")
        size = meta["parameters"]["chunk_chars"]
        if size not in chunk_cache:
            settings = Settings(data_dir=corpus.parent, kb_path=corpus, chunk_chars=size)
            chunk_cache[size] = {c.chunk_id: c for c in load_chunks(settings)}
        chunks = chunk_cache[size]
        raw_records = set()
        for raw_file in (folder / "raw").glob("*.json"):
            raw_value = json.loads(raw_file.read_text())
            if isinstance(raw_value, dict):
                for collection in ("rows", "results"):
                    if isinstance(raw_value.get(collection), list):
                        raw_records.update(digest(canonical(record)) for record in raw_value[collection])
        checked_sources, technical_failures = 0, 0
        for question, answer, row in zip(questions, answers, results):
            response = row.get("response") or row.get("core_response")
            if not response or response["answer"] != answer["answer"] or row["answer"] != answer["answer"]:
                raise ValueError("Ответ CSV не совпадает с сохранённым результатом")
            request = parse_question(question["question"]).model_dump()
            observed = row.get("request")
            if observed is None:
                conversation = row["effective_conversation"]
                observed = {"message": conversation[-1]["content"], "history": conversation[:-1]}
            if request != observed:
                raise ValueError("Вопрос CSV отличается от реально записанного CoreRequest")
            technical_failures += row["status"] in {"technical_failure", "blocked"}
            variants = [response["sources"]]
            original = row.get("original_record", {})
            if original:
                if digest(canonical(original)) not in raw_records:
                    raise ValueError("Нормализованный экспорт не соответствует исходному raw-журналу")
                original_response = original.get("response") or original.get("csv")
                if original_response != response or original["request"] != observed:
                    raise ValueError("Нормализация изменила ответ или запрос исходного журнала")
            for channel in ("api", "csv"):
                if channel in original:
                    variants.append(original[channel]["sources"])
            selected = [s for s in sources if s["id"] == question["id"]]
            if response["sources"] not in [s["sources"] for s in selected]:
                raise ValueError("Источники ответа не совпадают с экспортом")
            for record in selected:
                if record["sources"] not in variants:
                    raise ValueError("Экспорт добавил источники, которых нет в сохранённом результате")
                if record["turn"] != sum(t["role"] == "user" for t in request["history"]) + 1:
                    raise ValueError("Неверный номер пользовательского хода")
                for source in record["sources"]:
                    chunk = chunks.get(source["chunk_id"])
                    if chunk is None or source["document_id"] != chunk.document_id:
                        raise ValueError("Неизвестный ID источника")
                    if any(source.get(field) != getattr(chunk, field) for field in ("title", "section", "page")):
                        raise ValueError("Координаты источника не совпадают с корпусом")
                    if not source["excerpt"] or normalized(source["excerpt"]) not in normalized(chunk.text):
                        raise ValueError("Цитата отсутствует в указанном фрагменте корпуса")
                    checked_sources += 1
        episodes_count = 0
        if (folder / "transcripts.jsonl").exists():
            if (folder / "results.jsonl").read_bytes() != (folder / "raw/results.jsonl").read_bytes():
                raise ValueError("Живой журнал изменён при экспорте")
            by_id = {row_id(row): row for row in results}
            episodes = read_cases(folder / "transcripts.jsonl")
            episode_ids = [identity for e in episodes for identity in e["case_ids"]]
            if len(episode_ids) != len(ids) or set(episode_ids) != set(ids):
                raise ValueError("Неполное покрытие живых ходов")
            for episode in episodes:
                first = by_id[episode["case_ids"][0]]
                if bool(first["case"].get("reset_before")) != episode["reset_before_first_recorded"]:
                    raise ValueError("Изменён флаг сброса")
                for index, identity in enumerate(episode["case_ids"], 1):
                    row = by_id[identity]
                    full = row["effective_conversation"] + [{"role": "assistant", "content": row["answer"]}]
                    if episode["transcript"][:2*index] != full:
                        raise ValueError("Живой transcript не соответствует фактическим предыдущим ответам")
            episodes_count = len(episodes)
        if (folder / "contexts.jsonl").exists():
            contexts = read_cases(folder / "contexts.jsonl")
            if len(contexts) != len(ids) or {c["id"] for c in contexts} != set(ids):
                raise ValueError("Неполное покрытие сохранённых контекстов")
            by_id = {row_id(row): row for row in results}
            if any(c["payload"] != by_id[c["id"]]["context_sent"] for c in contexts):
                raise ValueError("Экспорт контекста отличается от исходного журнала")
        runtime_matches = {}
        revision = meta["code_revision"]
        if revision and re.fullmatch(r"[0-9a-f]{40}", revision):
            for name, wanted in meta["runtime_code_hashes_recorded"].items():
                path = name.replace("\\", "/")
                command = subprocess.run(["git", "show", revision + ":" + path], cwd=ROOT, capture_output=True)
                runtime_matches[path] = runtime_hash_match(command.stdout, wanted) if command.returncode == 0 else "git_object_unavailable"
        reports.append({"dataset": folder_name, "rows": len(ids), "source_records": len(sources),
                        "source_citations_verified": checked_sources, "technical_failures_recorded": technical_failures,
                        "live_episodes": episodes_count, "code_revision": meta["code_revision"],
                        "runtime_hashes_recorded": bool(meta["runtime_code_hashes_recorded"]),
                        "runtime_matches": runtime_matches,
                        "runtime_identity_checked": bool(runtime_matches),
                        "runtime_identity_verified": bool(runtime_matches) and all(v in {"exact", "git_lf_runtime_crlf"} for v in runtime_matches.values()),
                        "full_model_request_captured": meta["full_model_request_captured"],
                        "run_started_at_recorded": meta["run_started_at"] is not None,
                        "semantic_verdict": "not_reviewed"})
    first, second = passport["datasets"][:2]
    paired_inputs_equal = (root / first["folder"] / "questions.csv").read_bytes() == (root / second["folder"] / "questions.csv").read_bytes()
    report = {"status": "integrity_verified", "manifest_sha256": digest((root / "SHA256SUMS.json").read_bytes()),
              "corpus_sha256": corpus_hash, "files_verified": len(expected), "rows_verified": sum(r["rows"] for r in reports),
              "datasets": reports, "first_pair_inputs_byte_identical": paired_inputs_equal,
              "new_model_calls": 0, "new_search_calls": 0, "quality_acceptance": "pending"}
    save(private_path(output), report)
    return report


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
    command = commands.add_parser("audit-handoff")
    command.add_argument("--packet", type=Path, required=True)
    command.add_argument("--corpus", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("blind-handoff")
    command.add_argument("--packet", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            report = freeze(args.cases, args.corpus, args.output, args.source_revision)
        elif args.command == "audit":
            report = audit(args.bundle, args.expected_manifest_sha256)
        elif args.command == "export-development":
            report = export_development(args.bundle, args.output, args.expected_manifest_sha256)
        elif args.command == "audit-handoff":
            report = audit_handoff(args.packet, args.corpus, args.output)
        elif args.command == "blind-handoff":
            report = blind_handoff(args.packet, args.output, args.key_file)
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
