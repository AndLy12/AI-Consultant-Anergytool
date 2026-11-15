# Модель Данька: доступ команды через интернет

Рабочий адрес на 15 ноября 2026:
`https://65d7c46ca4278b.app.lhr.life/v1`.
Модель: **`energytool-eval`**. Актуальные открытые параметры —
[TEAM_MODEL_ENDPOINT.json](TEAM_MODEL_ENDPOINT.json). Адрес временный.

Данёк передаёт участникам отдельно **TEAM_API_CONNECTION.txt** с ключом.
Это ключ нашего тестового шлюза. В Git лежат код и инструкция, секрет хранится
отдельно. `127.0.0.1:1234` с чужого компьютера не ведёт на ПК Данька.

## Подключение из PowerShell

```powershell
$env:LLM_BASE_URL='https://65d7c46ca4278b.app.lhr.life/v1'
$env:LLM_API_KEY='<ключ из закрытого файла Данька>'
$env:LLM_MODEL='energytool-eval'
$env:CORE_DEMO='false'
$env:TEMPERATURE='0'
$env:MAX_TOKENS='1200'
$env:MAX_WORKERS='1'
```

Установить переменные нужно в той консоли, из которой запускается Python.
Само сохранение строк в `.env` не гарантирует, что конкретный скрипт их прочитает.

Проверка доступа без расхода генерации:

```python
import os
import httpx

base = os.environ['LLM_BASE_URL'].rstrip('/')
headers = {'Authorization': 'Bearer ' + os.environ['LLM_API_KEY']}
response = httpx.get(base + '/models', headers=headers, timeout=20)
response.raise_for_status()
print(response.json())
```

`200` и `energytool-eval` означают, что адрес и ключ приняты шлюзом.
Без ключа/с неверным ключом ожидается `401`. Генерация выполняется через
`POST /v1/chat/completions`, `stream=false`, максимум 1200 выходных токенов.
OpenAI-совместимый клиент тоже подходит: задать ему эти `base_url`, `api_key`, `model`.

## Как проверять именно V7

Этот адрес предоставляет **модель**, а поиск, история, инструкции и проверка
ответа выполняются кодом ядра у участника. Взять актуальную ветку
`codex/core-intent-rag`, зависимости проекта и закрытый корпус у Данька.
Для распакованного корпуса из QA-пакета задать `CORE_KB_PATH` на каталог `corpus`.

```powershell
$env:CORE_KB_PATH='C:/путь/к/закрытому/пакету/corpus'
```

```python
from consultant_core import create_core
from core.config import Settings
from core_contract import CoreRequest

core = create_core(Settings.from_environment())
try:
    result = core.respond(CoreRequest(message='Панели не нужны, нужен учёт. С чего начать?'))
    print(result.model_dump_json(indent=2))
finally:
    core.close()
```

Скрипт `check_core_development.py` текущего цикла привязан к локальному LM Studio
и сохраняет локальный паспорт. Для удалённого доступа не запускать его как будто
на компьютере участника есть `127.0.0.1:1234`. Использовать показанный вызов ядра,
`solution.py` с окружением или согласованный удалённый runner.

## Очередь, ошибки и срок работы

Один полный прогон за раз. `429` означает, что модель занята другим запросом;
согласовать очередь с Даньком. `504` — модель не уложилась во время, а не успешный
пустой ответ. Административные методы LM Studio через этот адрес не доступны.

Компьютер Данька, загруженная модель, шлюз и SSH-туннель должны оставаться
включёнными. Не переводить ПК в сон. Сервер работает отдельными процессами;
для его использования участникам не нужен чат Codex на ПК Данька.
Бесплатный домен localhost.run может смениться через несколько часов или
после перезапуска. Если адрес изменился, взять новый у Данька и обновить окружение.

## Даньку и следующему исполнителю: запуск/остановка

Скрипт `scripts/start_team_model_api.ps1` запускает скрытые процессы, сохраняет
PID и ключ в выделенном `work/team-model-api`. По умолчанию используется SSH
localhost.run. Cloudflare на этой машине выдавал TLS handshake EOF; тот адрес
не использовать. LM Studio остаётся на loopback, туннель ведёт к шлюзу 1235.

```powershell
$baseCheckout='C:/Users/user/Documents/Codex/2026-11-12/files-mentioned-by-the-user-plan/outputs/mts-energytool-core'
& ./scripts/start_team_model_api.ps1 -PythonExe "$baseCheckout/.venv/Scripts/python.exe" -PrivateDirectory "$baseCheckout/work/team-model-api"
```

`-Stop` закрывает доступ команды и оставляет LM Studio работающим.
`-RotateKey` перезапускает доступ с новым ключом: передать новый закрытый файл
участникам. После перезапуска проверить внешний `/models` и реальную генерацию,
обновить ссылку в этой инструкции и закоммитить только открытый endpoint JSON/MD.
Ключ, конфигурацию `work`, журналы с данными и QA-архив в публичный Git не добавлять.
