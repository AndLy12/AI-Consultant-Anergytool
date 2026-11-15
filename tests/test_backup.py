"""Копия WAL, восстановление аккаунта/заявки и отказ без потери исходных файлов."""

from contextlib import closing
import json
import sqlite3
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest

from backend.app import create_app
from backend.backup import main, snapshot, verify
from backend.core import StubCore


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / "source.db"
    url = "sqlite:///" + str(path)
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    return path, url


def test_online_backup_restores_login_history_and_application(source, tmp_path):
    path, url = source
    credentials = {"email": "backup@example.test", "password": "Backup-test-password-123"}
    with TestClient(create_app(url, StubCore())) as client:
        session = client.post("/api/auth/register", json=credentials)
        client.headers["X-CSRF-Token"] = session.json()["csrf_token"]
        dialog = client.post("/api/dialogs").json()["id"]
        assert client.post(f"/api/dialogs/{dialog}/messages", json={
            "request_id": str(uuid4()), "content": "Вымышленная проверка копии",
        }).status_code == 200
        card_path = f"/api/dialogs/{dialog}/application"
        card = client.put(card_path, json={"full_name": "Тестовый Клиент", "phone": "+7 000 000-00-00",
                                          "company": "Тест копии", "summary": "Вымышленный запрос"}).json()
        created = client.post(card_path + "/confirm", json={"version": card["version"], "consent": True}).json()
        # Открытое соединение удерживает WAL: копирование только source.db здесь ненадёжно.
        with closing(sqlite3.connect(path)) as keeper:
            assert keeper.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            backup = tmp_path / "backup.db"
            report = snapshot(path, backup)
            assert report["counts"]["users"] == 1 and report["counts"]["messages"] == 2
            assert backup.stat().st_mode & 0o777 == 0o600
    restored = tmp_path / "restored.db"
    assert snapshot(backup, restored) == report
    with TestClient(create_app("sqlite:///" + str(restored), StubCore())) as client:
        assert client.post("/api/auth/login", json=credentials).status_code == 200
        assert len(client.get(f"/api/dialogs/{dialog}").json()["messages"]) == 2
        assert client.get(card_path).json()["id"] == created["id"]
        assert client.get(card_path).json()["status"] == "created"


def test_existing_destination_and_source_are_never_overwritten(source, tmp_path):
    path, _ = source
    destination = tmp_path / "keep.db"
    destination.write_bytes(b"must-not-change")
    with pytest.raises(FileExistsError):
        snapshot(path, destination)
    assert destination.read_bytes() == b"must-not-change"
    with pytest.raises(FileExistsError):
        snapshot(path, path)
    verify(path)


@pytest.mark.parametrize("kind", ["missing", "corrupt", "wrong_schema", "broken_link"])
def test_invalid_source_leaves_no_false_successful_copy(source, tmp_path, kind):
    path, _ = source
    invalid = tmp_path / "invalid.db"
    if kind == "corrupt":
        invalid.write_bytes(b"not a SQLite database")
    elif kind == "wrong_schema":
        with closing(sqlite3.connect(invalid)) as connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
    elif kind == "broken_link":
        invalid = path
        with closing(sqlite3.connect(invalid)) as connection:
            connection.execute("INSERT INTO dialogs (id,user_id,status,version,created_at) VALUES ('bad','missing','active',0,'now')")
            connection.commit()
    output = tmp_path / "rejected.db"
    with pytest.raises((ValueError, sqlite3.Error)):
        snapshot(invalid, output)
    assert not output.exists()


def test_cli_supports_verify_restore_and_refuses_postgres(source, tmp_path, monkeypatch, capsys):
    path, _ = source
    backup = tmp_path / "copy.db"
    assert main(["create", "--output", str(backup)]) == 0
    assert main(["verify", "--source", str(backup)]) == 0
    assert main(["restore", "--source", str(backup), "--output", str(tmp_path / "restored.db")]) == 0
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:do-not-print@host/db")
    assert main(["create", "--output", str(tmp_path / "not-sqlite.db")]) == 2
    output = capsys.readouterr().out
    assert "do-not-print" not in output
    assert json.loads(output.splitlines()[-1])["status"] == "failed"
