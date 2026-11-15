"""Операторские команды не удаляют историю и не оставляют прежние права сотрудника."""

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import insert, select

from backend import db
from backend.app import create_app
from backend.core import StubCore
from backend.manage import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "manage.db")
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    with TestClient(create_app(url, StubCore())) as browser:
        session = browser.post("/api/auth/register", json={
            "email": "operator@example.test", "password": "Operator-test-password-123",
        }).json()
        browser.headers["X-CSRF-Token"] = session["csrf_token"]
        yield browser


def test_revoke_staff_applies_to_existing_session_and_preserves_dialog(client):
    dialog = client.post("/api/dialogs").json()["id"]
    assert client.get("/api/staff/dialogs").status_code == 403
    main(["grant-staff", "--email", " OPERATOR@EXAMPLE.TEST "])
    assert client.get("/api/staff/dialogs").status_code == 200
    main(["revoke-staff", "--email", "operator@example.test"])
    assert client.get("/api/staff/dialogs").status_code == 403
    assert client.get("/api/auth/me").json()["user"]["is_staff"] is False
    assert client.get(f"/api/dialogs/{dialog}").status_code == 200


def test_cleanup_expired_sessions_preserves_valid_login(client):
    with client.app.state.engine.begin() as connection:
        user_id = connection.execute(select(db.users.c.id)).scalar_one()
        connection.execute(insert(db.sessions).values(token_hash="a" * 64, user_id=user_id,
            csrf_token="b" * 64, expires_at="2000-01-01T00:00:00+00:00"))
    main(["purge-expired-sessions"])
    main(["purge-expired-sessions"])
    assert client.get("/api/auth/me").status_code == 200
    with client.app.state.engine.connect() as connection:
        assert len(connection.execute(select(db.sessions)).all()) == 1
        assert len(connection.execute(select(db.users)).all()) == 1


def test_unknown_account_is_not_created_by_role_command(client):
    with pytest.raises(SystemExit):
        main(["grant-staff", "--email", "unknown@example.test"])
    assert client.get("/api/auth/me").json()["user"]["is_staff"] is False
    with client.app.state.engine.connect() as connection:
        assert len(connection.execute(select(db.users)).all()) == 1
