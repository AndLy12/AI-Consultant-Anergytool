"""Реальные вызовы ядра через HTTP backend и CSV с одной и той же историей."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.app import create_app
from consultant_core import create_core
from core.config import Settings
from core.dialogue import parse_question
from core_contract import CoreRequest
import solution


class RecordingCore:
    """Сохраняем фактические входы и ответы, каждый respond обращается к модели."""
    def __init__(self, core):
        self.core = core
        self.calls = []

    def respond(self, request, **options):
        recorded = CoreRequest.model_validate(request).model_copy(deep=True)
        started = time.monotonic()
        response = self.core.respond(recorded, **options)
        self.calls.append((recorded, response, time.monotonic() - started))
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings.from_environment()
    root = Path(__file__).resolve().parents[1]
    source_hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in [*sorted((root / 'core').glob('*.py')), root / 'solution.py',
                               root / 'consultant_core.py', root / 'core_contract.py']}
    if settings.demo:
        parser.error("Для проверки требуется CORE_DEMO=false")
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    core = create_core(settings)
    traced = RecordingCore(core)
    pairs, inputs = [], []
    try:
        with tempfile.TemporaryDirectory(prefix="energytool-api-check-") as tmp:
            database = "sqlite:///" + str(Path(tmp) / "check.sqlite3")
            with patch.dict(os.environ, {"DATABASE_URL": database, "COOKIE_SECURE": "false"}):
                migration = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                command.upgrade(migration, "head")
                with TestClient(create_app(database, traced)) as client:
                    account = client.post("/api/auth/register", json={
                        "email": "local-check@example.test", "password": "Synthetic-password-123"})
                    account.raise_for_status()
                    client.headers["X-CSRF-Token"] = account.json()["csrf_token"]
                    for case in protocol["comparisons"]:
                        dialog = client.post("/api/dialogs")
                        dialog.raise_for_status()
                        for message in case["planned_user_steps"]:
                            body = {"request_id": str(uuid4()), "content": message}
                            response = client.post(f"/api/dialogs/{dialog.json()['id']}/messages", json=body)
                            response.raise_for_status()
                        effective, answer, elapsed = traced.calls[-1]
                        # Повтор того же HTTP-запроса должен вернуть сохранённый результат.
                        count = len(traced.calls)
                        repeated = client.post(f"/api/dialogs/{dialog.json()['id']}/messages", json=body)
                        repeated.raise_for_status()
                        assert len(traced.calls) == count and repeated.json()["content"] == response.json()["content"]
                        text = json.dumps([t.model_dump() for t in effective.history] +
                                          [{"role": "user", "content": effective.message}], ensure_ascii=False)
                        assert parse_question(text) == effective
                        inputs.append((case["comparison_id"], text))
                        pairs.append({"id": case["comparison_id"], "request": effective.model_dump(),
                                      "api": answer.model_dump(), "api_seconds": elapsed, "idempotent": True})
        questions = args.output / "questions.csv"
        with questions.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("id", "question"))
            writer.writerows(inputs)
        before = len(traced.calls)
        solution.run(solution.Settings(settings.data_dir, questions, args.output / "answers.csv", max_workers=1), traced)
        for pair, (request, answer, elapsed) in zip(pairs, traced.calls[before:], strict=True):
            pair.update(requests_equal=request.model_dump() == pair["request"],
                        csv=answer.model_dump(), csv_seconds=elapsed)
        assert all(pair["requests_equal"] for pair in pairs)
        report = {"pairs": len(pairs), "requests_equal": sum(p["requests_equal"] for p in pairs),
                  "model": settings.model, "source_sha256": source_hashes, "results": pairs}
        (args.output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k not in {"results", "source_sha256"}}, ensure_ascii=False))
    finally:
        core.close()


if __name__ == "__main__":
    main()
