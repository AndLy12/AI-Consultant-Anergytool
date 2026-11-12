import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


def session(client):
    response = client.post('/api/session', json={})
    assert response.status_code == 200
    return response


def chat(client):
    return client.post('/api/chats', json={}).json()['id']


def send(client, cid, content='Тестовое сообщение', rid=None, suffix=''):
    return client.post(f'/api/chats/{cid}/messages{suffix}',
                       json={'content': content, 'request_id': rid or str(uuid4())})


@pytest.fixture
def setup(tmp_path):
    path = str(tmp_path / 'test.sqlite3')
    return path, create_app(path)


def test_checkpoint_and_persistence(setup):
    path, app = setup
    with TestClient(app) as client:
        response = session(client)
        assert 'HttpOnly' in response.headers['set-cookie']
        assert 'SameSite=strict' in response.headers['set-cookie']
        cid = chat(client)
        result = send(client, cid)
        assert result.status_code == 200
        assert result.json()['result']['status'] == 'stub'
        assert len(client.get(f'/api/chats/{cid}').json()['messages']) == 2
        cookies = dict(client.cookies)
    with TestClient(create_app(path)) as restarted:
        restarted.cookies.update(cookies)
        assert len(restarted.get(f'/api/chats/{cid}').json()['messages']) == 2
        assert restarted.get('/health').json()['core_mode'] == 'stub'


def test_session_isolation_read_write_archive_reopen(setup):
    _, app = setup
    with TestClient(app) as alice, TestClient(app) as bob:
        session(alice)
        session(bob)
        cid = chat(alice)
        send(alice, cid, 'PRIVATE_A')
        assert bob.get('/api/chats').json()['chats'] == []
        for response in [bob.get(f'/api/chats/{cid}'), send(bob, cid),
                         bob.post(f'/api/chats/{cid}/archive', json={}),
                         bob.post(f'/api/chats/{cid}/reopen', json={})]:
            assert response.status_code == 404
            assert 'PRIVATE_A' not in response.text
        assert alice.get(f'/api/chats/{cid}').json()['archived'] is False


def test_no_session_and_forged_session(setup):
    _, app = setup
    with TestClient(app) as client:
        assert client.get('/api/chats').status_code == 401
        client.cookies.set('energytool_session', 'fake')
        assert client.get('/api/chats').status_code == 401


def test_no_trusted_roles_or_blank_message(setup):
    _, app = setup
    with TestClient(app) as client:
        session(client)
        cid = chat(client)
        response = client.post(f'/api/chats/{cid}/messages', json={
            'content': 'PRIVATE_INPUT', 'request_id': str(uuid4()), 'role': 'system'})
        assert response.status_code == 422
        assert 'PRIVATE_INPUT' not in response.text
        assert send(client, cid, '   ').status_code == 422
        assert send(client, cid, 'x' * 8001).status_code == 422
        assert send(client, cid, rid='bad').status_code == 422
        assert client.get(f'/api/chats/{cid}').json()['messages'] == []


def test_idempotency_and_conflict(setup):
    _, app = setup
    with TestClient(app) as client:
        session(client)
        cid, rid = chat(client), str(uuid4())
        first = send(client, cid, rid=rid).json()
        second = send(client, cid, rid=rid).json()
        assert first['result'] == second['result']
        assert second['replayed'] is True
        assert send(client, cid, 'Changed', rid).status_code == 409
        assert len(client.get(f'/api/chats/{cid}').json()['messages']) == 2


def test_archive_reopen_has_fresh_context(setup):
    _, app = setup
    with TestClient(app) as client:
        session(client)
        old = chat(client)
        send(client, old)
        assert client.post(f'/api/chats/{old}/archive', json={}).status_code == 200
        assert send(client, old).status_code == 409
        new = client.post(f'/api/chats/{old}/reopen', json={}).json()['id']
        assert new != old
        assert client.get(f'/api/chats/{new}').json()['messages'] == []
        assert len(client.get(f'/api/chats/{old}').json()['messages']) == 2


class RecordingCore:
    def __init__(self):
        self.calls = []

    async def answer(self, history):
        self.calls.append(history)
        return {'answer': 'Нужно уточнение.', 'status': 'clarification_needed'}


def test_core_gets_only_current_chat_history(tmp_path):
    core = RecordingCore()
    with TestClient(create_app(str(tmp_path / 'db'), core)) as client:
        session(client)
        a, b = chat(client), chat(client)
        send(client, a, 'ONE')
        send(client, a, 'TWO')
        send(client, b, 'OTHER')
    assert [(m.role, m.content) for m in core.calls[1]] == [
        ('user', 'ONE'), ('assistant', 'Нужно уточнение.'), ('user', 'TWO')]
    assert [m.content for m in core.calls[2]] == ['OTHER']


class BrokenCore:
    def __init__(self, mode):
        self.mode = mode

    async def answer(self, history):
        if self.mode == 'timeout':
            await asyncio.sleep(1)
        if self.mode == 'error':
            raise RuntimeError('SECRET_MUST_NOT_LEAK')
        return {'answer': 'Unsupported claim', 'status': 'answered', 'sources': []}


@pytest.mark.parametrize('mode,status', [('timeout', 504), ('error', 502), ('invalid', 502)])
def test_failed_core_does_not_store_fake_answer(tmp_path, mode, status):
    with TestClient(create_app(str(tmp_path / 'db'), BrokenCore(mode), timeout=.01)) as client:
        session(client)
        cid, rid = chat(client), str(uuid4())
        for _ in range(2):
            response = send(client, cid, rid=rid)
            assert response.status_code == status
            assert 'SECRET' not in response.text
        history = client.get(f'/api/chats/{cid}').json()['messages']
        assert len(history) == 1
        assert history[0]['state'] == 'failed'


def test_sse_and_security_headers(setup):
    _, app = setup
    with TestClient(app) as client:
        session(client)
        cid = chat(client)
        response = send(client, cid, suffix='/stream')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/event-stream')
        assert 'event: result' in response.text and 'event: done' in response.text
        assert response.headers['cache-control'] == 'no-store'
        assert "script-src 'self'" in client.get('/').headers['content-security-policy']
        assert client.post('/api/session', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/session', content='x').status_code == 415
        assert client.post('/api/session', content='x' * 40001, headers={'content-type': 'application/json'}).status_code == 413


def test_credentials_hashed_and_session_expiry(setup):
    path, app = setup
    with TestClient(app) as client:
        session(client)
        token = client.cookies.get('energytool_session')
        with sqlite3.connect(path) as db:
            stored = db.execute('SELECT token_hash FROM sessions').fetchone()[0]
            assert stored != token and len(stored) == 64
            db.execute('UPDATE sessions SET expires_at=0')
        assert client.get('/api/chats').status_code == 401


def test_parallel_turns_are_serialized(tmp_path):
    import threading
    entered = threading.Event()
    release = threading.Event()

    class SlowCore(RecordingCore):
        async def answer(self, history):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return await super().answer(history)

    with TestClient(create_app(str(tmp_path / 'db'), SlowCore())) as client:
        session(client)
        cid = chat(client)
        with ThreadPoolExecutor() as pool:
            future = pool.submit(send, client, cid, 'FIRST')
            assert entered.wait(3)
            try:
                assert send(client, cid, 'SECOND').status_code == 409
                assert client.post(f'/api/chats/{cid}/archive', json={}).status_code == 409
            finally:
                release.set()
            assert future.result(timeout=3).status_code == 200


def test_host_guard_and_static_assets(setup):
    _, app = setup
    with TestClient(app) as client:
        assert client.get('/health', headers={'host': 'evil.example'}).status_code == 400
        assert client.get('/app.js').status_code == 200
        assert client.get('/style.css').status_code == 200
        assert client.get('/openapi.json').status_code == 200
