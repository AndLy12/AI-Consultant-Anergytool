"""Синтетическая проверка независимости снимка и честности QA-счётчиков."""

from copy import deepcopy
import json
from pathlib import Path
import zipfile
from types import SimpleNamespace

import pytest

from scripts import role3_quality_eval as qa

TEXT = "# Учебный документ\n\n## Условия\n\nТестовый стенд экспортирует учебный отчёт.\n\n## Другой раздел\n\nСведения для другого условия."
QUOTE = "Тестовый стенд экспортирует учебный отчёт."


def scenario(number):
    """Нумерация здесь проверяет формат; это не содержательный независимый набор."""
    return {"case_id": f"{number:03}", "category": "synthetic", "independence_group": f"group-{number}",
            "intent": "Синтетическая проверка инструмента", "split": "final" if number < 20 else "development",
            "evaluation_mode": "single", "conversation": [{"role": "user", "content": f"Учебная ситуация {number}?"}],
            "criteria": [{"turn": 1, "severity": "substantial", "required_facts": ["Учебный экспорт"],
                          "forbidden_claims": ["Выдуманный формат"], "acceptable_alternatives": ["Краткий точный ответ"],
                          "sources": [{"document": "synthetic.md", "section": "Условия", "quote": QUOTE}]}]}


@pytest.fixture
def inputs(tmp_path):
    corpus = tmp_path / "knowledge-base.zip"
    with zipfile.ZipFile(corpus, "w") as archive:
        archive.writestr("synthetic.md", TEXT)
    source = tmp_path / "cases.jsonl"
    source.write_text("\n".join(json.dumps(scenario(i), ensure_ascii=False) for i in range(80)), encoding="utf-8")
    return source, corpus


def test_freeze_partition_hashes_and_existing_destination(inputs, tmp_path):
    source, corpus = inputs
    bundle = tmp_path / "frozen"
    report = qa.freeze(source, corpus, bundle, "a" * 40)
    assert report["partitions"] == {"development": 60, "final": 20}
    assert report["model_calls"] == 0
    assert report["semantic_alignment"] == "pending_independent_source_review"
    assert qa.audit(bundle, report["manifest_sha256"]) == report
    assert (bundle / "final.jsonl").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        qa.freeze(source, corpus, bundle, "a" * 40)
    assert qa.audit(bundle)["manifest_sha256"] == report["manifest_sha256"]


def test_modified_data_and_modified_manifest_are_detected(inputs, tmp_path):
    source, corpus = inputs
    bundle = tmp_path / "frozen"
    report = qa.freeze(source, corpus, bundle, "a" * 40)
    path = bundle / "final.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="изменился"):
        qa.audit(bundle)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["final.jsonl"] = qa.digest(path.read_bytes())
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Паспорт"):
        qa.audit(bundle, report["manifest_sha256"])


@pytest.mark.parametrize("mutation", ["duplicate_id", "duplicate_input", "cross_split", "missing_fact", "missing_source", "wrong_section", "wrong_quote", "missing_turn", "assistant_last"])
def test_invalid_or_leaky_scenarios_rejected(mutation):
    cases = [scenario(0), scenario(21)]
    rule = cases[1]["criteria"][0]
    if mutation == "duplicate_id":
        cases[1]["case_id"] = cases[0]["case_id"]
    elif mutation == "duplicate_input":
        cases[1]["conversation"] = deepcopy(cases[0]["conversation"])
    elif mutation == "cross_split":
        cases[1]["independence_group"] = cases[0]["independence_group"]
    elif mutation == "missing_fact":
        rule["required_facts"] = []
    elif mutation == "missing_source":
        rule["sources"] = []
    elif mutation == "wrong_section":
        rule["sources"][0]["section"] = "Другой раздел"
    elif mutation == "wrong_quote":
        rule["sources"][0]["quote"] = "Вымышленное обещание без основания."
    elif mutation == "missing_turn":
        rule["turn"] = 3
    else:
        cases[1]["conversation"][-1]["role"] = "assistant"
    with pytest.raises(ValueError):
        qa.validate_cases(cases, {"synthetic.md": TEXT})


def test_live_chain_has_no_invented_previous_answers():
    case = scenario(0)
    case["evaluation_mode"] = "live_chain"
    case["conversation"].append({"role": "user", "content": "А ограничения?"})
    second = deepcopy(case["criteria"][0])
    second["turn"] = 2
    case["criteria"].append(second)
    qa.validate_cases([case], {"synthetic.md": TEXT})
    case["conversation"].insert(1, {"role": "assistant", "content": "Заранее выдуманный ответ"})
    with pytest.raises(ValueError, match="Живая цепочка"):
        qa.validate_cases([case], {"synthetic.md": TEXT})


def test_wilson_interval_is_not_false_certainty():
    low, high = qa.wilson(95, 100)
    assert low == pytest.approx(.88825, abs=.0001)
    assert high == pytest.approx(.97846, abs=.0001)
    assert qa.wilson(1, 1)[0] < .3
    with pytest.raises(ValueError):
        qa.wilson(0, 0)


def test_public_destination_is_rejected():
    with pytest.raises(ValueError, match="игнорируемой"):
        qa.private_path(qa.ROOT / "docs" / "must-not-publish.json")


def test_excluded_product_matches_required_guidance_not_only_document_title():
    hit = SimpleNamespace(required=True, chunk=SimpleNamespace(title="Учебная подготовка", section="Для солнечной станции"))
    assert qa.excluded_required(hit, ["солнечные электростанции"])
    assert not qa.excluded_required(hit, ["облачная платформа"])
    hit.required = False
    assert not qa.excluded_required(hit, ["солнечные электростанции"])


def test_export_development_never_contains_final_scenarios(inputs, tmp_path):
    source, corpus = inputs
    bundle = tmp_path / "frozen"
    frozen = qa.freeze(source, corpus, bundle, "a" * 40)
    exported = tmp_path / "handoff"
    report = qa.export_development(bundle, exported, frozen["manifest_sha256"])
    assert report["scenarios"] == 60 and report["final_included"] is False
    assert {p.name for p in exported.iterdir()} == {"development.jsonl", "live-chains.jsonl", "questions.csv", "passport.json"}
    rows = qa.read_cases(exported / "development.jsonl")
    assert all(row["split"] == "development" for row in rows)
    assert "000" not in {row["case_id"] for row in rows}
    assert len(rows) == 60
    with pytest.raises(FileExistsError):
        qa.export_development(bundle, exported, frozen["manifest_sha256"])
