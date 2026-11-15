"""Готовность БД/ядра отдельно от доступности внешней модели."""

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from backend.app import create_app
from backend.core import MissingCore, StubCore


@pytest.fixture
def app(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "ready.db")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CORE_MODE", "stub")
    command.upgrade(Config("alembic.ini"), "head")
    return create_app(url, StubCore())


def test_ready_does_not_call_model(app):
    def forbidden(_request):
        raise AssertionError("Опрос готовности не должен генерировать ответ")
    with TestClient(app) as client:
        app.state.core.respond = forbidden
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json()["checks"] == {"database": True, "core": True}
        assert response.json()["provider_checked"] is False
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/health").json()["status"] == "ok"


def test_missing_core_is_not_ready_but_health_contract_remains(app):
    with TestClient(app) as client:
        app.state.core = MissingCore()
        assert client.get("/ready").status_code == 503
        assert client.get("/ready").json()["checks"] == {"database": True, "core": False}
        health = client.get("/health")
        assert health.status_code == 200 and health.json()["core_ready"] is False


@pytest.mark.parametrize("change", ["DELETE FROM alembic_version", "UPDATE alembic_version SET version_num='old-revision'", "DROP TABLE users"])
def test_missing_or_outdated_schema_is_not_ready(app, change):
    with TestClient(app) as client:
        with app.state.engine.begin() as connection:
            connection.execute(text(change))
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"] == {"database": False, "core": True}
        assert "sqlite" not in response.text and "old-revision" not in response.text
