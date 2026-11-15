"""Аудит входящего пакета на синтетических данных, включая согласованную подмену.

Ни ответов команды, ни закрытых цитат здесь нет. Повторно подписанные хеши
не должны скрывать рассогласование CSV, исходного журнала и настоящего источника.
"""

from copy import deepcopy
import csv
import json
from pathlib import Path
import zipfile

import pytest

from core.config import Settings
from core.documents import load_chunks
from scripts import role3_quality_eval as qa

FACT = "Учебный стенд сохраняет вымышленный отчёт."


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def seal(root):
    """Эмулируем заново подписанный транспорт, не меняя внутренние доказательства."""
    for folder in sorted(root.glob("part-*")):
        path = folder / "passport.json"
        passport = json.loads(path.read_text())
        passport["questions_sha256"] = qa.digest((folder / "questions.csv").read_bytes())
        passport["files_sha256"] = {p.relative_to(folder).as_posix(): qa.digest(p.read_bytes())
                                      for p in folder.rglob("*") if p.is_file() and p != path}
        write_json(path, passport)
    write_json(root / "SHA256SUMS.json", {p.relative_to(root).as_posix(): qa.digest(p.read_bytes())
               for p in root.rglob("*") if p.is_file() and p != root / "SHA256SUMS.json"})


@pytest.fixture
def packet(tmp_path):
    root = tmp_path / "packet"
    root.mkdir()
    corpus = tmp_path / "knowledge-base.zip"
    with zipfile.ZipFile(corpus, "w") as archive:
        archive.writestr("synthetic.md", "# Учебный стенд\n\n## Условия\n\n" + FACT)
    chunk = load_chunks(Settings(data_dir=tmp_path, kb_path=corpus))[0]
    source = {"document_id": chunk.document_id, "chunk_id": chunk.chunk_id,
              "title": chunk.title, "excerpt": FACT, "section": chunk.section, "page": chunk.page}
    datasets = []
    for index in (1, 2):
        folder = root / f"part-{index}"
        folder.mkdir()
        question = "Есть ли учебный отчёт?"
        request = {"message": question, "history": []}
        response = {"answer": FACT, "sources": [deepcopy(source)]}
        raw = {"id": "001", "request": request, "response": response, "seconds": .2}
        write_json(folder / "raw" / "probe.json", {"rows": [raw]})
        with (folder / "questions.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream); writer.writerow(["id", "question"]); writer.writerow(["001", question])
        with (folder / "answers.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream); writer.writerow(["id", "answer"]); writer.writerow(["001", FACT])
        write_json(folder / "results.jsonl", {"id": "001", "request": request, "response": response,
                   "answer": FACT, "status": "answered", "original_record": raw})
        write_json(folder / "sources.jsonl", {"id": "001", "turn": 1, "sources": [source]})
        write_json(folder / "passport.json", {"rows": 1, "parameters": {"chunk_chars": 1800},
                   "corpus_sha256": qa.digest(corpus.read_bytes()), "code_revision": "a" * 40,
                   "runtime_code_hashes_recorded": {}, "full_model_request_captured": False,
                   "run_started_at": None})
        datasets.append({"folder": folder.name, "answers": "answers.csv", "rows": 1})
    write_json(root / "passport.json", {"datasets": datasets, "corpus_sha256": qa.digest(corpus.read_bytes())})
    seal(root)
    return root, corpus, tmp_path / "audit.json"


def test_packet_audit_preserves_ids_and_does_not_claim_semantic_acceptance(packet):
    root, corpus, output = packet
    report = qa.audit_handoff(root, corpus, output)
    assert report["rows_verified"] == 2
    assert report["first_pair_inputs_byte_identical"] is True
    assert report["quality_acceptance"] == "pending"
    assert report["new_model_calls"] == report["new_search_calls"] == 0
    assert report["datasets"][0]["runtime_identity_verified"] is False
    assert report["datasets"][0]["source_citations_verified"] == 1


@pytest.mark.parametrize("mutation", ["answer", "request", "raw", "source", "source_title", "turn", "duplicate_source", "duplicate_result"])
def test_resigned_transport_cannot_hide_internal_mismatch(packet, mutation):
    root, corpus, output = packet
    folder = root / "part-1"
    row = json.loads((folder / "results.jsonl").read_text())
    if mutation == "answer":
        (folder / "answers.csv").write_text("id,answer\n001,Изменённый ответ\n", encoding="utf-8")
    elif mutation == "request":
        (folder / "questions.csv").write_text("id,question\n001,Другой вопрос\n", encoding="utf-8")
    elif mutation == "raw":
        row["original_record"]["seconds"] = 900
        write_json(folder / "results.jsonl", row)
    elif mutation in {"source", "source_title"}:
        # Меняем все связанные записи согласованно; независимый корпус всё равно выявит подмену.
        field = "excerpt" if mutation == "source" else "title"
        for response in (row["response"], row["original_record"]["response"]):
            response["sources"][0][field] = "Вымышленное утверждение не из источника."
        write_json(folder / "results.jsonl", row)
        write_json(folder / "raw/probe.json", {"rows": [row["original_record"]]})
        source = json.loads((folder / "sources.jsonl").read_text())
        source["sources"][0][field] = "Вымышленное утверждение не из источника."
        write_json(folder / "sources.jsonl", source)
    elif mutation == "turn":
        source = json.loads((folder / "sources.jsonl").read_text()); source["turn"] = 2
        write_json(folder / "sources.jsonl", source)
    elif mutation == "duplicate_source":
        source = (folder / "sources.jsonl").read_text()
        (folder / "sources.jsonl").write_text(source + "\n" + source)
    else:
        line = (folder / "results.jsonl").read_text()
        (folder / "results.jsonl").write_text(line + "\n" + line)
    seal(root)
    with pytest.raises(ValueError):
        qa.audit_handoff(root, corpus, output)
    assert not output.exists()


def test_added_nested_checksum_file_is_not_silently_ignored(packet):
    root, corpus, output = packet
    write_json(root / "unexpected/SHA256SUMS.json", {"extra": True})
    with pytest.raises(ValueError, match="Состав"):
        qa.audit_handoff(root, corpus, output)


def test_packet_paths_and_symlinks_are_rejected(packet, tmp_path):
    root, corpus, output = packet
    with pytest.raises(ValueError):
        qa.packet_file(root, "../knowledge-base.zip")
    (root / "link").symlink_to(corpus)
    with pytest.raises(ValueError, match="Ссылки"):
        qa.audit_handoff(root, corpus, output)


def test_only_documented_line_endings_are_accepted_for_runtime_identity():
    blob = b"# synthetic\nvalue = 1\n"
    assert qa.runtime_hash_match(blob, qa.digest(blob)) == "exact"
    assert qa.runtime_hash_match(blob, qa.digest(blob.replace(b"\n", b"\r\n"))) == "git_lf_runtime_crlf"
    assert qa.runtime_hash_match(blob, qa.digest(blob.replace(b"1", b"2"))) == "mismatch"


def test_blind_handoff_hides_mapping_and_swaps_both_orders(packet, tmp_path):
    root, corpus, _ = packet
    output, key_path = tmp_path / "blind", tmp_path / "private-key.json"
    report = qa.blind_handoff(root, output, key_path)
    assert report["key_included"] is False and report["orders"] == 2
    key = json.loads(key_path.read_text())
    first = key["mapping"]["order-1"]["001"]
    second = key["mapping"]["order-2"]["001"]
    assert first["A"] == second["B"] and first["B"] == second["A"]
    assert first["A"] != first["B"]
    for path in output.rglob("*"):
        if path.is_file():
            assert "part-1" not in path.read_text() and "part-2" not in path.read_text()
    assert qa.read_cases(output / "order-1/sources.jsonl")[0]["id"] == "001"
    assert len((output / "assessments.csv").read_text().splitlines()) == 1
    with pytest.raises((ValueError, FileExistsError)):
        qa.blind_handoff(root, output, key_path)


def test_blind_key_cannot_be_placed_inside_shared_folder(packet, tmp_path):
    root, _, _ = packet
    output = tmp_path / "blind"
    with pytest.raises(ValueError, match="Ключ"):
        qa.blind_handoff(root, output, output / "key.json")
    assert not output.exists()
