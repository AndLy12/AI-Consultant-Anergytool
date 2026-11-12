# МТС Energytool — консультант

Ветка `core`: общее Python-ядро для веба и `solution.py`, совместимое с актуальным командным backend.

Подробная инструкция: [core/README.md](core/README.md). Задачи и границы работы: [core/PLAN.md](core/PLAN.md). Результат проверок: [core/REPORT.md](core/REPORT.md).

## Демонстрация ядра

Нужен Python 3.12+. Настоящие данные и API пока не предоставлены; демонстрация явно использует вымышленный «Учебный стенд Альфа», без утверждений о свойствах Energytool.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:CORE_DEMO = 'true'
.\.venv\Scripts\python.exe solution.py --questions examples/core-demo/questions.csv --output work/demo-submission.csv
```

По умолчанию демонстрация выключена. Для обычного режима нужны закрытая база, URL, ключ и модель из окружения. Отсутствие API или ошибка не приводят к скрытой замене модели.

## Веб с подключённым ядром

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-core-web.txt
$env:CORE_MODE = 'real'
$env:CORE_DEMO = 'true'
.\.venv\Scripts\python.exe -m backend.check_core --message 'Как экспортировать отчёт стенда Альфа?'
.\.venv\Scripts\python.exe run.py
```

Веб-режим использует lock `requirements-web.txt` и дополнительные зависимости ядра. Платформенный кандидат использует собственный `requirements.txt`; устанавливать два противоречащих Pydantic-pin одним вызовом pip не нужно.

Backend реализует аккаунты, историю с разных устройств, диалоги и подтверждённые заявки. Новый HTTP API использует `/api/dialogs`, а не ранний `/api/chats`. В публичном клиенте нет ключа модели и права самостоятельно создавать заявку через текст ответа.

- [Запуск backend](docs/BACKEND.md).
- [Архитектурные решения](docs/DECISIONS.md).
- [Общий контракт ядра](docs/CORE_INTEGRATION.md).
- [Статус backend-разработчика](docs/ROLE1_STATUS.md).

## Проверки и платформенная поставка

```powershell
.\.venv\Scripts\python.exe -m pip install pytest==8.3.5
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/build_core_submission.py --output work/core-submission.zip
```

Сборщик формирует плоский ZIP без backend, веб-БД, документов, вопросов, индексов, frontend и секретов. Код из `core/` переносится в корневые модули с преобразованными импортами. Приём платформой, её whitelist, среда Python и реальные ресурсы пока не проверены.

В репозитории только исходный код и синтетические примеры. Закрытые документы, контакты, история и ключи не публикуются. Развёртывание в интернете, CRM и почта не подключены. Изменения этой ветки не объединены с `main` автоматически.
