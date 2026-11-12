# Изученные материалы

Дата: 12 ноября 2026 (МСК). Область: первая контрольная точка backend МТС Energytool.

Прочитана целиком пользовательская инструкция `CODEX_AI_CONSULTANT_INSTRUCTIONS.md`, версия 1.0 от 10 ноября 2026.
Материалы ПСБ исключены из требований по прямому уточнению владельца.
Оригинал инструкции не копируется в репозиторий: здесь зафиксированы применённые требования и интерфейсы команды.

Открыты все семь прямых ссылок из инструкции:

| Источник | Что учтено |
|---|---|
| [rank-bm25](https://github.com/dorianbrown/rank_bm25) | Простой BM25 baseline — зона участника №2; не зависимость веба |
| [bm25s](https://github.com/xhluca/bm25s) | Альтернатива BM25, выбирать по измерениям |
| [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) | Модель эмбеддингов; доступность прокси ещё не проверена |
| [RecursiveCharacterTextSplitter](https://docs.langchain.com/oss/python/integrations/splitters/recursive_text_splitter) | Размер по умолчанию измеряется в символах; не внедрён заранее |
| [FAISS](https://github.com/facebookresearch/faiss) | Поиск по векторам; не генерация эмбеддингов, сейчас не нужен |
| [Pydantic](https://docs.pydantic.dev/) | Проверка схемы результата; не проверка истинности |
| [Promptfoo Python](https://www.promptfoo.dev/docs/providers/python/) | Необязательная локальная оценка, не runtime-зависимость |

Дополнительно просмотрены официальные разделы [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/), [cookie](https://fastapi.tiangolo.com/advanced/response-cookies/) и [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/).

Не предоставлены в рамках текущего запроса: исходный регламент DOCX, оригинальный requirements.txt организаторов, demo CSV, sample_submission.zip, knowledge-base.zip и подтверждённые параметры API. Их содержимое, работоспособность и результат платформы не заявляются проверенными.
В Downloads найден отдельный PDF кейса МТС; он не является исходным DOCX-регламентом и не использован для утверждений об API или данных. Для backend-заглушки внешние API не требуются.
