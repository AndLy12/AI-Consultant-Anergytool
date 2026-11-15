"""Настоящее синхронное ядро + HTTP-мок Qwen + сохранение ответа в backend.

Мок заменяет только транспорт: поиск, промпт, проверка цитаты, API и БД реальные.
Данные вымышленные, соединения с внешним провайдером не создаются.
"""

import json
from uuid import uuid4
import httpx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from backend.app import create_app
from core.config import Settings
from core.engine import EnergytoolCore
from core.llm import QwenClient


def test_qwen_contract_response_saved_and_client_closed(tmp_path,monkeypatch):
    kb = tmp_path/'knowledge-base'
    kb.mkdir()
    text = 'Вымышленный стенд Бета поддерживает экспорт отчёта в CSV.'
    (kb/'demo.md').write_text(text,encoding='utf-8')
    settings = Settings(data_dir=tmp_path,base_url='https://qwen.invalid/v1',
                        api_key='test-only-placeholder',model='test-qwen')
    requests = []
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        payload = json.loads(body['messages'][-1]['content'])
        doc = payload['documents'][0]
        raw = {'status':'answered','evidence':[{'chunk_id':doc['chunk_id'],'quote':text}]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(raw)},'finish_reason':'stop'}]})
    client = QwenClient(settings,transport=httpx.MockTransport(handler))
    closed = []
    monkeypatch.setattr(client,'close',lambda:closed.append(True))
    core = EnergytoolCore(settings,client=client)
    url = 'sqlite:///'+str(tmp_path/'backend.db')
    monkeypatch.setenv('DATABASE_URL',url)
    monkeypatch.setenv('CORE_MODE','real')
    command.upgrade(Config('alembic.ini'),'head')
    with TestClient(create_app(url,core)) as browser:
        account = browser.post('/api/auth/register',json={'email':'qwen-test@example.test','password':'Test-password-123'}).json()
        browser.headers['X-CSRF-Token'] = account['csrf_token']
        assert browser.get('/health').json()['core_demo'] is False
        dialog = browser.post('/api/dialogs').json()['id']
        response = browser.post(f'/api/dialogs/{dialog}/messages',json={'request_id':str(uuid4()),'content':'Как экспортировать отчёт стенда Бета в CSV?'})
        assert response.status_code == 200,response.text
        assert response.json()['sources'][0]['excerpt'] == text
        assert 'Демонстрационный режим.' not in response.json()['content']
        assert len(browser.get(f'/api/dialogs/{dialog}').json()['messages']) == 2
    assert closed == [True]
    assert str(requests[0].url) == 'https://qwen.invalid/v1/chat/completions'
    assert requests[0].headers['Authorization'] == 'Bearer test-only-placeholder'
