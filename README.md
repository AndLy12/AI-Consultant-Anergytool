# МТС Energytool — веб-прототип и общее ядро

Backend хранит аккаунты, сессии, историю и подтверждённые заявки. Ядро Данька
подключено через consultant_core.create_core(); тот же код используется solution.py.
Каталог core, фабрика, платформенный скрипт и сборщик перенесены без изменений.

## Три режима

| Настройки | Результат |
| --- | --- |
| CORE_MODE=stub | Backend с постоянным тестовым ответом, без ядра |
| CORE_MODE=real, CORE_DEMO=true | Ядро, BM25 и цитаты; вымышленные документы и тестовая модель |
| CORE_MODE=real, CORE_DEMO=false | Закрытая база и Qwen API; нужны настройки провайдера |

real означает загрузку фабрики, не доказательство вызова Qwen. Demo виден в UI и /health.

## Локально: Python 3.12

```bash
python3.12 -m venv .venv-core
source .venv-core/bin/activate
pip install -r requirements-dev.txt
CORE_MODE=real CORE_DEMO=true python run.py
```

В текущем checkout .venv-core уже создана на Python 3.12; прежняя .venv на 3.14
сохранена. Открыть http://localhost:8000. Вопрос: «Как экспортировать отчёт стенда
Альфа в CSV?». Миграции применяются автоматически, аккаунты/история остаются в SQLite.

## Docker

Чистая копия без .env запускается с заглушкой: `docker compose up --build --wait`.
Для подключённого demo-ядра:

```bash
CORE_MODE=real CORE_DEMO=true docker compose up --build --wait
```

Для настоящей базы задайте LLM_BASE_URL, LLM_API_KEY, LLM_MODEL и KNOWLEDGE_DIR —
абсолютный путь к закрытой папке с knowledge-base.zip или каталогом knowledge-base:

```bash
docker compose -f compose.yaml -f compose.knowledge.yaml up --build --wait
```

Дополнение подключает базу read-only и отключает demo. Документы и ключи не
попадают в образ. Новый код/окружение применяйте через up, простой restart их не обновит.
SQLite хранится в постоянном томе; down без --volumes сохраняет данные.

## Проверки

```bash
python -m pytest -q
CORE_MODE=real CORE_DEMO=true python -m backend.check_core --message 'Как экспортировать отчёт стенда Альфа?'
```

pytest включает unittest и tests/core Данька. Старый baseline заменён: его CLI/HTTP
тесты приведены к актуальному контракту. CI использует Python 3.12 и обе БД;
Docker проверяет stub и demo отдельно. Python 3.10 к новому ядру не относится.

## Материалы команды

- [Backend и API](docs/BACKEND.md).
- [Контракт подключения ядра](docs/CORE_INTEGRATION.md).
- [Фактический результат интеграции](docs/CORE_INTEGRATION_RESULT.md).
- [Описание ядра Данька](core/README.md).
- [Архитектурные решения](docs/DECISIONS.md).

Frontend Даши подключается к /api/auth/* и /api/dialogs/*. Черновик резюме ядра
не создаёт заявку: пользователь подтверждает карточку, backend выполняет запись.
Реальный Qwen пока не проверен: локально нет адреса, ключа и модели. CRM, почта
и авторизация МТС не подключены. Настоящие данные и секреты не публикуются.

Платформенный requirements.txt использует Pydantic 2.14.2, веб-lock — 2.14.3;
не устанавливайте их одновременно в одно окружение. Платформенная оценка и
полный запуск на реальном API не подтверждены интеграционными тестами.
