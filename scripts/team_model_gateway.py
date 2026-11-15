"""Временный защищённый доступ команды к одной локальной модели.

Это инструмент разработки, отдельный от backend проекта. Туннель направляется
на этот loopback-сервис; административные API LM Studio не проксируются.
Ключ берётся из закрытого файла, заголовки и тексты запросов не журналируются.
"""
import argparse
import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
import httpx
import uvicorn


@dataclass(frozen=True)
class GatewaySettings:
    token: str = field(repr=False)
    upstream: str = 'http://127.0.0.1:1234/v1'
    model: str = 'energytool-eval'
    port: int = 1235
    max_tokens: int = 1200
    max_body_bytes: int = 256 * 1024
    timeout_seconds: float = 105


def create_app(settings, transport=None):
    """Один запрос к модели одновременно; очередь не забивает чужие дедлайны."""
    if len(settings.token) < 32:
        raise ValueError('Нужен случайный ключ не короче 32 символов')
    target = urlsplit(settings.upstream)
    if target.scheme != 'http' or target.hostname != '127.0.0.1' or target.path.rstrip('/') != '/v1':
        raise ValueError('Проксируется только локальный LM Studio /v1')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    lock = asyncio.Lock()
    accepted = []
    allowed = {'model', 'messages', 'temperature', 'max_tokens', 'stream', 'response_format',
               'chat_template_kwargs', 'reasoning_effort', 'stop', 'top_p', 'seed',
               'presence_penalty', 'frequency_penalty'}

    def error(code, message):
        return JSONResponse({'error': {'message': message, 'type': 'team_gateway_error'}},
                            status_code=code, headers={'Cache-Control': 'no-store'})

    @app.middleware('http')
    async def authenticate(request, call_next):
        if request.url.path == '/' and request.method == 'GET':
            return JSONResponse({'service': 'Team model API', 'authentication': 'Bearer token required'})
        supplied = request.headers.get('authorization', '')
        if not secrets.compare_digest(supplied.encode(), ('Bearer ' + settings.token).encode()):
            return JSONResponse({'error': {'message': 'Нужен действующий ключ команды'}},
                status_code=401, headers={'WWW-Authenticate': 'Bearer', 'Cache-Control': 'no-store'})
        return await call_next(request)

    @app.get('/health')
    async def health():
        return {'status': 'running', 'model': settings.model, 'busy': lock.locked()}

    @app.get('/v1/models')
    async def models():
        return {'object': 'list', 'data': [{'id': settings.model, 'object': 'model', 'owned_by': 'team-local'}]}

    @app.post('/v1/chat/completions')
    async def complete(request: Request):
        body = bytearray()
        async for part in request.stream():
            if len(body) + len(part) > settings.max_body_bytes:
                return error(413, 'Запрос слишком большой')
            body.extend(part)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            return error(400, 'Нужен корректный JSON')
        if not isinstance(payload, dict) or set(payload) - allowed:
            return error(400, 'Неподдерживаемые поля запроса')
        if payload.get('model', settings.model) != settings.model:
            return error(400, 'Доступна только выделенная модель команды')
        if payload.get('stream', False) is not False:
            return error(400, 'Для этого временного доступа используйте stream=false')
        tokens = payload.get('max_tokens', settings.max_tokens)
        if type(tokens) is not int or not 1 <= tokens <= settings.max_tokens:
            return error(400, f'max_tokens должен быть от 1 до {settings.max_tokens}')
        messages = payload.get('messages')
        if (not isinstance(messages, list) or not 1 <= len(messages) <= 128
                or any(not isinstance(m, dict) or set(m) - {'role', 'content', 'name'}
                       or m.get('role') not in {'system', 'user', 'assistant'}
                       or not isinstance(m.get('content'), str) for m in messages)):
            return error(400, 'Нужны текстовые messages с ролями system/user/assistant')
        if lock.locked():
            result = error(429, 'Модель занята: прогоны команды запускаются по очереди')
            result.headers['Retry-After'] = '3'
            return result
        now = time.monotonic()
        accepted[:] = [stamp for stamp in accepted if now - stamp < 60]
        if len(accepted) >= 20:
            return error(429, 'Не более 20 запросов модели в минуту')
        await lock.acquire()
        accepted.append(now)
        try:
            payload = dict(payload, model=settings.model, max_tokens=tokens, stream=False)
            async with asyncio.timeout(settings.timeout_seconds):
                async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
                    async with client.stream('POST', settings.upstream.rstrip('/') + '/chat/completions',
                            headers={'Authorization': 'Bearer local-verification-placeholder'},
                            json=payload, timeout=settings.timeout_seconds) as response:
                        content = bytearray()
                        async for part in response.aiter_bytes():
                            if len(content) + len(part) > 512 * 1024:
                                return error(502, 'Ответ модели превысил лимит')
                            content.extend(part)
                        if 300 <= response.status_code < 400:
                            return error(502, 'Переадресация провайдера не поддерживается')
                        return Response(bytes(content), status_code=response.status_code,
                            media_type='application/json', headers={'Cache-Control': 'no-store'})
        except (TimeoutError, httpx.TimeoutException):
            return error(504, 'Модель не завершила ответ за отведённое время')
        except httpx.HTTPError:
            return error(502, 'Локальная модель временно недоступна')
        finally:
            lock.release()

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    settings = GatewaySettings(**json.loads(args.config.read_text(encoding='utf-8')))
    uvicorn.run(create_app(settings), host='127.0.0.1', port=settings.port,
                access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
