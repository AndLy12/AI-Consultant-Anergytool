"""Приёмка сервера: настоящая фабрика/HTTP/БД с локальным имитатором Qwen.

В отличие от MockTransport здесь запрос проходит через TCP в отдельный HTTP-сервер.
Используются только вымышленные документы и ключ; внешняя модель не вызывается.
Проверяем сборку роли 1, а не качество выбора ответа моделью.
"""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Event, Thread
import time
from uuid import uuid4

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
FACT = "Вымышленный стенд Гамма экспортирует отчёт в CSV."
FAKE_KEY = "release-test-key-not-a-real-secret"


@pytest.fixture
def provider():
    """Имитируем только провайдера; сохраняем запросы для проверки изоляции."""
    state = {"mode": "ok", "calls": [], "entered": Event(), "release": Event()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # HTTP-заголовки и полный контекст не должны попадать в общий журнал CI.
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append({
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "payload": payload,
            })
            mode = state["mode"]
            if mode == "hold":
                # Даём тесту убить собственный backend ровно во время генерации,
                # а не угадываем этот момент произвольной паузой.
                state["entered"].set()
                state["release"].wait(timeout=5)
            status = int(mode) if mode in {"401", "429", "503"} else 200
            context = json.loads(payload["messages"][-1]["content"])
            document = context["documents"][0]
            selection = {"status": "answered", "evidence": [{
                "chunk_id": document["chunk_id"],
                "quote": "Вымышленная неподтверждённая возможность." if mode == "bad_quote" else FACT,
            }]}
            body = {"choices": [{"message": {"content": json.dumps(selection)}, "finish_reason": "stop"}]}
            if status != 200:
                body = {"error": FAKE_KEY + " upstream-private-details"}
            encoded = b"not-json" if mode == "malformed" else json.dumps(body).encode()
            if mode == "timeout":
                time.sleep(.35)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                # Ожидаемо для сценария таймаута: клиент уже отменил запрос.
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/provider/custom"
    try:
        yield state
    finally:
        state["release"].set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def release(tmp_path, provider):
    """Запускаем отдельный backend; повторный старт использует ту же временную БД."""
    knowledge = tmp_path / "knowledge-base"
    knowledge.mkdir()
    (knowledge / "synthetic.md").write_text("# Вымышленный стенд Гамма\n\n" + FACT, encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    # Не наследуем настройки реальной модели, пользовательской БД или frontend.
    # Настройки окружения здесь полностью принадлежат одноразовому прогону.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("LLM_", "OPENAI_", "CORE_"))}
    env.update(
        DATABASE_URL="sqlite:///" + str(tmp_path / "release.db"),
        DATA_DIR=str(tmp_path), CORE_KB_PATH=str(knowledge),
        CORE_MODE="real", CORE_MODULE="consultant_core", CORE_DEMO="false",
        LLM_BASE_URL=provider["url"], LLM_API_KEY=FAKE_KEY, LLM_MODEL="synthetic-qwen",
        LLM_MAX_RETRIES="1", LLM_TIMEOUT_SECONDS="0.1", CORE_TIMEOUT_SECONDS="3",
        APP_ORIGIN=origin, COOKIE_SECURE="false", GEN_MODEL="",
        TOP_K="5", CHUNKER="sections", CORE_CHUNK_CHARS="1800",
        CORE_CONTEXT_CHARS="14000", CORE_HISTORY_CHARS="16000",
        TEMPERATURE="0.1", MAX_TOKENS="1200", MAX_WORKERS="4",
        NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    env.pop("FRONTEND_DIST", None)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   cwd=ROOT, env=env, check=True, capture_output=True, timeout=30)

    @contextmanager
    def start():
        """Останавливаем только собственный процесс, не рабочий прототип на 8000."""
        process_env = dict(env)
        if provider["mode"] == "hold":
            # В тесте аварии ждём сигнал от провайдера, а не сетевой таймаут.
            # Увеличение относится только к этому одноразовому процессу.
            process_env.update(LLM_TIMEOUT_SECONDS="30", CORE_TIMEOUT_SECONDS="30")
        with (tmp_path / "backend.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen([
                sys.executable, "-m", "uvicorn", "backend.app:create_app", "--factory",
                "--host", "127.0.0.1", "--port", str(port),
            ], cwd=ROOT, env=process_env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 20
                with httpx.Client(base_url=origin, timeout=5, trust_env=False) as probe:
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError("Тестовый backend не запустился")
                        try:
                            health = probe.get("/health").json()
                            assert health["core_ready"] and not health["core_demo"]
                            break
                        except httpx.TransportError:
                            if time.monotonic() >= deadline:
                                raise RuntimeError("Истекло ожидание тестового backend") from None
                            time.sleep(.05)
                yield process
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    with httpx.Client(base_url=origin, timeout=10, trust_env=False) as device:
        yield start, device


def register(device, email="release@example.test"):
    """Устройство хранит cookie и отдельный CSRF-токен серверной сессии."""
    credentials = {"email": email, "password": "Release-test-password-123"}
    response = device.post("/api/auth/register", json=credentials)
    assert response.status_code == 201
    device.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return credentials


def send(device, dialog, content, request_id=None):
    return device.post(f"/api/dialogs/{dialog}/messages", json={
        "request_id": request_id or str(uuid4()), "content": content,
    })


def test_release_history_devices_restart_and_application(release, provider):
    """Проверяем совместный путь, повторы и восстановление без подмены фабрики."""
    start, device = release
    with start():
        credentials = register(device)
        dialog = device.post("/api/dialogs").json()["id"]
        request_id = str(uuid4())
        response = send(device, dialog, "Как стенд Гамма экспортирует отчёт в CSV?", request_id)
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["sources"][0]["excerpt"] == FACT
        assert "Демонстрационный режим" not in answer["content"]
        assert send(device, dialog, "Как стенд Гамма экспортирует отчёт в CSV?", request_id).json()["id"] == answer["id"]
        assert len(provider["calls"]) == 1
        call = provider["calls"][0]
        assert call["path"] == "/provider/custom/chat/completions"
        assert call["authorization"] == "Bearer " + FAKE_KEY
        assert call["payload"]["model"] == "synthetic-qwen"
        assert credentials["password"] not in json.dumps(call["payload"])
        assert send(device, dialog, "А какой формат экспорта отчёта?").status_code == 200
        context = json.loads(provider["calls"][-1]["payload"]["messages"][-1]["content"])
        assert len(context["history"]) == 2
        card_path = f"/api/dialogs/{dialog}/application"
        card = device.put(card_path, json={"full_name": "Тестовый Клиент", "phone": "+7 000 000-00-00",
                                         "company": "Тестовая компания", "summary": "Проверка экспорта CSV"}).json()
        confirmation = {"version": card["version"], "consent": True}
        saved = device.post(card_path + "/confirm", json=confirmation).json()
        assert saved["status"] == "created"
    # Вход и БД переживают остановку отдельного процесса, не только новый TestClient.
    with start():
        assert device.get("/api/auth/me").status_code == 200
        assert len(device.get(f"/api/dialogs/{dialog}").json()["messages"]) == 4
        assert device.post(card_path + "/confirm", json=confirmation).json()["id"] == saved["id"]
        with httpx.Client(base_url=device.base_url, timeout=10, trust_env=False) as second:
            session = second.post("/api/auth/login", json=credentials)
            assert session.status_code == 200
            assert second.get(card_path).json()["id"] == saved["id"]
        with httpx.Client(base_url=device.base_url, timeout=10, trust_env=False) as stranger:
            register(stranger, "stranger@example.test")
            assert stranger.get(f"/api/dialogs/{dialog}").status_code == 404
            assert stranger.get(card_path).status_code == 404
        fresh = device.post("/api/dialogs").json()["id"]
        assert send(device, fresh, "Как стенд Гамма экспортирует отчёт в CSV?").status_code == 200
        context = json.loads(provider["calls"][-1]["payload"]["messages"][-1]["content"])
        assert context["history"] == []


@pytest.mark.parametrize("mode,calls", [
    ("401", 1), ("429", 2), ("503", 2), ("malformed", 1), ("bad_quote", 1), ("timeout", 2),
])
def test_release_provider_failure_saved_and_recoverable(release, provider, mode, calls):
    """Сбой API не создаёт ответ, не раскрывает ключ и не блокирует новую попытку."""
    start, device = release
    with start():
        register(device)
        dialog = device.post("/api/dialogs").json()["id"]
        provider["mode"] = mode
        request_id = str(uuid4())
        response = send(device, dialog, "Как стенд Гамма экспортирует отчёт в CSV?", request_id)
        assert response.status_code == 503
        assert FAKE_KEY not in response.text and "upstream-private-details" not in response.text
        assert len(provider["calls"]) == calls
        messages = device.get(f"/api/dialogs/{dialog}").json()["messages"]
        assert len(messages) == 1 and messages[0]["status"] == "failed"
        assert send(device, dialog, "Как стенд Гамма экспортирует отчёт в CSV?", request_id).status_code == 409
        assert len(provider["calls"]) == calls
        provider["mode"] = "ok"
        recovered = send(device, dialog, "Как стенд Гамма экспортирует отчёт в CSV?")
        assert recovered.status_code == 200, recovered.text
        context = json.loads(provider["calls"][-1]["payload"]["messages"][-1]["content"])
        assert context["history"] == []  # Неуспешный обмен хранится, но не идёт в модель.


def test_release_abrupt_crash_during_generation_recovers_without_duplicate(release, provider):
    """После SIGKILL processing становится failed, новая отправка не зависает."""
    start, device = release
    provider["mode"] = "hold"
    with start() as process:
        register(device)
        dialog = device.post("/api/dialogs").json()["id"]
        request_id = str(uuid4())
        provider["mode"] = "hold"
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(send, device, dialog, "Как стенд Гамма экспортирует отчёт в CSV?", request_id)
            assert provider["entered"].wait(timeout=5)
            # Это только Popen нашего теста; порт/процесс рабочего прототипа не затрагивается.
            process.kill()
            process.wait(timeout=5)
            provider["release"].set()
            with pytest.raises(httpx.TransportError):
                pending.result(timeout=5)
    provider["mode"] = "ok"
    calls_before = len(provider["calls"])
    with start():
        messages = device.get(f"/api/dialogs/{dialog}").json()["messages"]
        assert len(messages) == 1 and messages[0]["status"] == "failed"
        assert len(provider["calls"]) == calls_before  # Автоповтора платного API нет.
        assert send(device, dialog, "Как стенд Гамма экспортирует отчёт в CSV?", request_id).status_code == 409
        assert send(device, dialog, "Как стенд Гамма экспортирует отчёт в CSV?").status_code == 200
        messages = device.get(f"/api/dialogs/{dialog}").json()["messages"]
        assert len(messages) == 3 and sum(item["role"] == "assistant" for item in messages) == 1
