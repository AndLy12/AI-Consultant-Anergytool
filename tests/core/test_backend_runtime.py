"""Проверка настоящей фабрики demo-ядра через новый HTTP API backend."""

from uuid import uuid4
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from backend.app import create_app


def test_factory_sources_and_demo_warning_reach_http(tmp_path, monkeypatch):
    url = 'sqlite:///' + str(tmp_path / 'integration.sqlite3')
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('CORE_MODE', 'real')
    monkeypatch.setenv('CORE_MODULE', 'consultant_core')
    monkeypatch.setenv('CORE_DEMO', 'true')
    command.upgrade(Config('alembic.ini'), 'head')
    with TestClient(create_app(url)) as client:
        account = client.post('/api/auth/register', json={'email': 'synthetic@example.test', 'password': 'Test-password-123'})
        assert account.status_code == 201
        client.headers['X-CSRF-Token'] = account.json()['csrf_token']
        dialog = client.post('/api/dialogs').json()['id']
        response = client.post(f'/api/dialogs/{dialog}/messages', json={
            'request_id': str(uuid4()), 'content': 'Как экспортировать отчёт стенда Альфа в CSV?'})
        assert response.status_code == 200, response.text
        body = response.json()
        assert 'Демонстрационный режим.' in str(body)
        assert 'chunk_id' in str(body)
        assert 'вымышлен' in str(body)
        history = client.get(f'/api/dialogs/{dialog}').json()
        assert len(history['messages']) == 2
        assert 'Демонстрационный режим.' in str(history)
