# AI Consultant for Energytool

Каталог `core/` из командного main сохранён. Backend-заглушка заменена версией
с аккаунтами и заявками. `backend.main:create_app` и `python run.py` запускают
новую реализацию; старые `/api/chats` заменены `/api/dialogs` (см. docs/BACKEND.md).
После установки requirements-web.txt можно запустить `CORE_MODE=stub python run.py`;
миграции будут применены автоматически. Устанавливать два разных набора backend
зависимостей не нужно: requirements-backend.txt теперь ссылается на requirements-web.txt.

## Backend роли 1

Запуск веб-прототипа: [docs/BACKEND.md](docs/BACKEND.md).
Решения: [docs/DECISIONS.md](docs/DECISIONS.md).
Коллеге по роли 2: [docs/CORE_INTEGRATION.md](docs/CORE_INTEGRATION.md).
Статус: [docs/ROLE1_STATUS.md](docs/ROLE1_STATUS.md).

Реализованы аккаунты, история с разных устройств, API диалогов, подтверждение
карточки заявки, доступ сотрудника, миграции и проверочный интерфейс.
Ядро коллеги ещё не подключено. `CORE_MODE=stub` включает явную заглушку для
проверки backend, а не RAG-консультанта.

Ниже описан ранний solution.py — заготовка роли 2, не принятый платформой сабмит.
requirements.txt относится к нему; веб-зависимости — requirements-web.txt.

Воспроизводимый baseline первого этапа: решение читает вопросы, извлекает
релевантные фрагменты базы знаний, при наличии настроек обращается к
OpenAI-совместимой модели и создаёт `submission.csv`.

## Быстрый запуск

Требуется Python 3.10 или новее. Внешних зависимостей у baseline нет.

```bash
python solution.py --questions examples/questions.csv --output submission.csv
```

Для запуска с моделью:

```bash
export DATA_DIR=/path/to/data
export LLM_BASE_URL=https://provider.example
export LLM_API_KEY=secret
export LLM_MODEL=model-name
python solution.py
```

Если параметры модели не заданы, решение всё равно формирует непустые ответы:
возвращает наиболее релевантный фрагмент базы или просит уточнить вопрос.

## Переменные окружения

| Переменная | Назначение | Значение по умолчанию |
| --- | --- | --- |
| `DATA_DIR` | Каталог с `questions.csv` и базой знаний | текущий каталог |
| `QUESTIONS_PATH` | Явный путь к файлу вопросов | `DATA_DIR/questions.csv` |
| `OUTPUT_PATH` | Путь результата | `submission.csv` |
| `LLM_BASE_URL` | URL OpenAI-совместимого API | не задан |
| `LLM_API_KEY` | Ключ API | не задан |
| `LLM_MODEL` | Имя модели | не задано |
| `TOP_K` | Число фрагментов контекста | `4` |

Также поддерживаются стандартные альтернативы `OPENAI_BASE_URL`,
`OPENAI_API_KEY`, `OPENAI_MODEL`.

## Формат данных

Входной CSV обязан иметь колонку `id` и одну из колонок `question`, `query`,
`text`, `message`. Опционально поддерживается история в `history`, `dialogue`
или `context`. База знаний первого baseline: файлы `.txt`, `.md`, `.csv` и
`.json` внутри `DATA_DIR`.

Выход всегда имеет только две колонки:

```csv
id,answer
1,Текст ответа
```

## Проверка

**Всем участникам: [простая инструкция по тестам и CI](tests/README.md).**
`Project checks` проверяет скрипт сабмита на Python 3.10 и 3.12, а
`Backend SQLite and PostgreSQL` — полный проект на обеих БД.
Сценарии ручной оценки: [tests/QUALITY_CASES.md](tests/QUALITY_CASES.md).

```bash
python -m unittest discover -s tests -v
python solution.py --questions examples/questions.csv --output /tmp/submission.csv
```

Архитектурные границы для параллельной работы описаны в
[`ARCHITECTURE.md`](ARCHITECTURE.md), а текущая доска — в [`TASKS.md`](TASKS.md).
