"""Сквозная проверка Docker из чистой копии, без библиотек backend на хосте.

Создаём отдельный Compose-проект с уникальным именем и только вымышленными
данными. После проверки удаляются исключительно его контейнер и тестовый том.
Обычный том приложения не используется. Запуск: python scripts/docker_smoke.py.
"""

import http.cookiejar
import argparse
import json
import os
from pathlib import Path
import socket
import re
import subprocess
import urllib.request
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


def main():
    """Собираем образ, создаём заявку и проверяем сохранность после пересоздания."""
    parser = argparse.ArgumentParser(description="Проверка Docker с заглушкой или demo-ядром")
    parser.add_argument("--mode", choices=["stub", "demo"], default="stub")
    mode = parser.parse_args().mode
    if (ROOT / ".env").exists() or (ROOT / ".venv").exists():
        raise SystemExit("Проверку выполняют в чистой копии без .env и .venv")
    project = "energytool-smoke-" + uuid4().hex[:12]
    # Выбираем свободный локальный порт, чтобы не останавливать рабочий прототип.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {k:v for k,v in os.environ.items() if not k.startswith(("LLM_", "OPENAI_"))}
    env.update(CORE_MODE="stub" if mode == "stub" else "real", CORE_DEMO="true" if mode == "demo" else "false",
               DATABASE_URL="sqlite:////data/energytool.db",
               APP_PORT=str(port), APP_ORIGIN=origin, COOKIE_SECURE="false",
               CORE_MODULE="consultant_core", CORE_TIMEOUT_SECONDS="120", GEN_MODEL="")
    command = ["docker", "compose", "--project-name", project, "--file", str(ROOT / "compose.yaml")]

    def compose(*args, capture=False):
        # Аргументы передаются напрямую, без shell: имена проекта и путей не исполняются.
        return subprocess.run(command + list(args), cwd=ROOT, env=env, check=True,
                              text=True, capture_output=capture, timeout=900)

    def browser():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def api(client, path, method="GET", body=None, csrf=""):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(origin + path, data=data, method=method,
            headers={"Content-Type":"application/json", "X-CSRF-Token":csrf, "Origin":origin})
        with client.open(request, timeout=15) as response:
            payload = response.read()
            return json.loads(payload) if payload else None

    try:
        compose("config", "--quiet")
        compose("build", "--no-cache", "--pull")
        compose("up", "--detach", "--wait", "--wait-timeout", "120")
        client = browser()
        health = api(client, "/health")
        assert health["frontend"] == "react", "Production UI was not included in image"
        # Проверяем реальные хешированные JS/CSS сборки, а не только заглушку API.
        with client.open(origin, timeout=15) as response:
            html = response.read().decode()
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
        assert any(path.endswith('.js') for path in assets)
        assert any(path.endswith('.css') for path in assets)
        for path in assets:
            with client.open(origin + path, timeout=15) as response:
                assert response.status == 200 and response.read()
        assert health["core_mode"] == ("stub" if mode == "stub" else "real")
        assert health["core_demo"] == (mode == "demo")
        uid = compose("exec", "-T", "backend", "id", "-u", capture=True).stdout.strip()
        assert uid != "0", "Контейнер не должен работать под root"
        compose("exec", "-T", "backend", "python", "-c",
            "from pathlib import Path; assert all(not Path(p).exists() for p in "
            "['/app/.env', '/app/.venv', '/app/.venv-core', '/app/.git', '/app/Данные']), 'Private files leaked into image'")
        email = f"docker-{uuid4().hex}@example.test"
        credentials = {"email":email, "password":"Docker-test-password-123"}
        session = api(client, "/api/auth/register", "POST", credentials)
        csrf = session["csrf_token"]
        dialog = api(client, "/api/dialogs", "POST", csrf=csrf)["id"]
        response = api(client, f"/api/dialogs/{dialog}/messages", "POST",
            {"request_id":str(uuid4()), "content":"Как экспортировать отчёт стенда Альфа в CSV?"}, csrf)
        if mode == "stub":
            assert "Тестовый режим" in response["content"]
        else:
            assert "Демонстрационный режим." in response["content"]
            assert response["sources"] and response["warnings"]
        card_path = f"/api/dialogs/{dialog}/application"
        card = api(client, card_path, "PUT", {"full_name":"Тестовый Клиент",
            "phone":"+7 000 000-00-00", "company":"Тест Docker", "summary":"Проверка сохранности"}, csrf)
        confirmed = api(client, card_path+"/confirm", "POST", {"version":card["version"],"consent":True}, csrf)
        assert confirmed["status"] == "created"
        # down без --volumes удаляет контейнер; новый up обязан прочитать прежнюю БД.
        compose("down", "--timeout", "15")
        compose("up", "--detach", "--wait", "--wait-timeout", "120")
        second = browser()
        new_session = api(second, "/api/auth/login", "POST", credentials)
        history = api(second, f"/api/dialogs/{dialog}")
        assert len(history["messages"]) == 2
        saved = api(second, card_path)
        assert saved["id"] == confirmed["id"] and saved["status"] == "created"
        repeated = api(second, card_path+"/confirm", "POST",
            {"version":saved["version"],"consent":True}, new_session["csrf_token"])
        assert repeated["id"] == saved["id"]
        # Проверяем и сессию старого устройства: она тоже должна жить в томе БД.
        assert api(client, "/api/auth/me")["user"]["email"] == email
        print("Docker smoke OK: clean build, non-root, registration, history, application, persistent volume")
    except Exception:
        compose("logs", "--no-color", "--tail", "100")
        raise
    finally:
        # Удаляем только созданный здесь одноразовый проект с уникальным именем.
        compose("down", "--volumes", "--remove-orphans", "--timeout", "15")


if __name__ == "__main__":
    main()
