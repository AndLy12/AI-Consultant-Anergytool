"""Проверка раздачи frontend с API без Node и без изменения исходников Даши."""

from pathlib import Path
from unittest.mock import patch
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from backend.app import create_app


def build_fixture(root):
    root.mkdir()
    (root/'assets').mkdir()
    (root/'index.html').write_text('<html><script src="/assets/test.js"></script></html>',encoding='utf-8')
    (root/'assets/test.js').write_text('console.log("frontend fixture");',encoding='utf-8')
    return root


def test_built_frontend_and_api_have_same_origin(tmp_path,monkeypatch):
    dist=build_fixture(tmp_path/'dist')
    monkeypatch.setenv('FRONTEND_DIST',str(dist))
    monkeypatch.setenv('CORE_MODE','stub')
    url='sqlite:///'+str(tmp_path/'app.db')
    monkeypatch.setenv('DATABASE_URL',url)
    command.upgrade(Config('alembic.ini'),'head')
    with TestClient(create_app(url)) as client:
        page=client.get('/')
        assert '/assets/test.js' in page.text
        assert client.get('/assets/test.js').text.startswith('console.log')
        assert "script-src 'self'" in page.headers['content-security-policy']
        assert client.get('/health').json()['frontend']=='react'
        assert client.get('/api/unknown').status_code==404
        assert client.get('/assets/missing.js').status_code==404
        assert client.get('/assets/%2e%2e/.env').status_code==404
        assert client.get('/api/dialogs').status_code==401


def test_explicit_incomplete_build_fails_fast(tmp_path,monkeypatch):
    monkeypatch.setenv('FRONTEND_DIST',str(tmp_path/'missing'))
    with pytest.raises(RuntimeError,match='FRONTEND_DIST'):
        create_app('sqlite://')


def test_diagnostic_ui_remains_available_without_build(tmp_path,monkeypatch):
    monkeypatch.delenv('FRONTEND_DIST',raising=False)
    monkeypatch.setenv('CORE_MODE','stub')
    url='sqlite:///'+str(tmp_path/'app.db')
    monkeypatch.setenv('DATABASE_URL',url)
    command.upgrade(Config('alembic.ini'),'head')
    # Подменяем только поиск автоматической сборки, не файловые ответы diagnostic UI.
    original=Path.is_file
    def without_build(path):
        return False if path.parts[-3:]==('frontend','dist','index.html') else original(path)
    with patch.object(Path,'is_file',without_build),TestClient(create_app(url)) as client:
        assert client.get('/health').json()['frontend']=='diagnostic'
        assert 'Прототип консультации' in client.get('/').text
