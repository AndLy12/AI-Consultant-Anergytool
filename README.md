# AI Consultant for Energytool

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

```bash
python -m unittest discover -s tests -v
python solution.py --questions examples/questions.csv --output /tmp/submission.csv
```

Архитектурные границы для параллельной работы описаны в
[`ARCHITECTURE.md`](ARCHITECTURE.md), а текущая доска — в [`TASKS.md`](TASKS.md).

