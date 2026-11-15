"""Границы публичного тестового шлюза: ключ, модель, пути и ограничение нагрузки."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading

from fastapi.testclient import TestClient
import httpx

from scripts.team_model_gateway import GatewaySettings, create_app

TOKEN = 'synthetic-test-token-' + 'x' * 32
HEADERS = {'Authorization': 'Bearer ' + TOKEN}
PAYLOAD = {'model': 'energytool-eval', 'messages': [{'role': 'user', 'content': 'Тест'}], 'max_tokens': 20}


def test_authentication_and_administration_are_not_forwarded():
    calls = []
    def upstream(request):
        calls.append(request)
        assert request.headers['Authorization'] == 'Bearer local-verification-placeholder'
        assert TOKEN not in request.content.decode()
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Ответ'}}]})
    with TestClient(create_app(GatewaySettings(TOKEN), httpx.MockTransport(upstream))) as client:
        assert client.post('/v1/chat/completions', json=PAYLOAD).status_code == 401
        assert client.post('/v1/chat/completions', json=PAYLOAD,
                           headers={'Authorization': 'Bearer wrong'}).status_code == 401
        assert client.post('/api/v1/models/load', headers=HEADERS, json={}).status_code == 404
        assert not calls
        assert client.post('/v1/chat/completions', headers=HEADERS, json=PAYLOAD).status_code == 200
        assert len(calls) == 1
        assert client.get('/v1/models', headers=HEADERS).json()['data'][0]['id'] == 'energytool-eval'


def test_limits_reject_payload_before_contacting_model():
    def upstream(request):
        raise AssertionError('Невалидный запрос не должен достигнуть модели')
    with TestClient(create_app(GatewaySettings(TOKEN, max_body_bytes=1024), httpx.MockTransport(upstream))) as client:
        for changed in [dict(PAYLOAD, model='another-model'), dict(PAYLOAD, stream=True),
                        dict(PAYLOAD, max_tokens=1201), dict(PAYLOAD, tools=[]),
                        dict(PAYLOAD, max_tokens=True)]:
            assert client.post('/v1/chat/completions', headers=HEADERS, json=changed).status_code == 400
        assert client.post('/v1/chat/completions', headers=HEADERS, content='x' * 1025).status_code == 413


def test_busy_response_and_lock_release():
    entered = threading.Event()
    async def upstream(request):
        entered.set()
        await asyncio.sleep(.2)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Ответ'}}]})
    with TestClient(create_app(GatewaySettings(TOKEN), httpx.MockTransport(upstream))) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.post, '/v1/chat/completions', headers=HEADERS, json=PAYLOAD)
            assert entered.wait(2)
            assert client.post('/v1/chat/completions', headers=HEADERS, json=PAYLOAD).status_code == 429
            assert first.result().status_code == 200
        assert client.post('/v1/chat/completions', headers=HEADERS, json=PAYLOAD).status_code == 200
