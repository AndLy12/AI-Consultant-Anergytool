# API v0.1 — МТС Energytool

> Исторический контракт заглушки. Актуальное API с аккаунтами и заявками:
> [BACKEND.md](BACKEND.md), `/openapi.json`. Даша использует `/api/auth/*` и
> `/api/dialogs/*`; старые `/api/session`, `/api/chats` и SSE заменены.
> Интерфейс ядра `core.contracts.Consultant` сохранён и подключается адаптером.

Базовый адрес локального прототипа: `http://127.0.0.1:8000`.
Все POST отправляются как `application/json` (пустое тело операции: `{}`).
Интерактивная схема: `/docs`, машинная схема: `/openapi.json`.

## Сессия и права

`POST /api/session`, `{}` → `200 {"status":"ready"}`. Выставляет случайную HttpOnly-cookie `energytool_session`, SameSite=strict, 30 дней. Повтор с действующей cookie сохраняет владельца. Сам токен в JSON не выдаётся.
Для остальных `/api/*` требуется эта cookie (`fetch` с `credentials: 'same-origin'`). Нет сессии: `401 {"error":"session_required"}`.
Идентификатор диалога не заменяет проверку владельца. Чужой/неизвестный ID: одинаковый 404. Origin другого сайта отклоняется; CORS не включён.
У фронтенда и backend один origin: для Vite используйте dev-proxy `/api` на backend, а не выдачу ключей клиенту.

## Диалоги

| Метод | Путь | Тело / результат |
|---|---|---|
| POST | `/api/chats` | `{ "title": "Новый диалог" }` (title необязателен) → 201 объект диалога |
| GET | `/api/chats?offset=0` | `{ "chats": [], "offset": 0, "limit": 100 }` |
| GET | `/api/chats/{id}` | Объект диалога с `messages` |
| POST | `/api/chats/{id}/archive` | `{}` → `{ "status": "archived" }` |
| POST | `/api/chats/{id}/reopen` | `{}` → 201 **новый** пустой диалог, только из архива |

Объект диалога: `{ "id": "uuid", "title": "Новый диалог", "archived": false, "created_at": 1794480000.0 }`.
`created_at` — Unix timestamp UTC. Владелец и cookie не входят в ответ.
Сообщение пользователя: `id`, `role: user`, `content`, `request_id`, `state: processing|complete|failed`, `created_at`.
Сообщение консультанта: `id`, `role: assistant`, `content`, `result`, `created_at`.
Архив сохраняется; чтение архива не добавляет его в контекст активного диалога.

## Отправка сообщения

`POST /api/chats/{id}/messages`

```json
{"content":"Хочу разобраться в Energytool", "request_id":"cfc3c680-8d82-4d0b-b2ce-77c08656bc69"}
```

`content`: 1–8000 символов, не только пробелы. `request_id`: UUID, создаётся клиентом для одного логического сообщения.
При сетевом повторе сохраняйте тот же UUID и тот же текст. Изменённый текст с тем же UUID: 409 `idempotency_conflict`.
Другое сообщение при незавершённом текущем: 409 `chat_busy`. После 200 сообщение и ответ уже сохранены.
Запоздалый повтор failed-сообщения после более новых обращений: 409 `stale_retry`.

```json
{
  "request_id":"cfc3c680-8d82-4d0b-b2ce-77c08656bc69",
  "replayed":false,
  "result":{
    "answer":"Тестовый ответ: backend получил сообщение и историю текущего диалога. База знаний и модель пока не подключены; консультация по продуктам недоступна.",
    "status":"stub",
    "sources":[],
    "missing_information":[],
    "warnings":["Демонстрационная заглушка, не продуктовая консультация."]
  }
}
```

Статусы ядра: `answered`, `clarification_needed`, `insufficient_data`, `out_of_scope`. `stub` разрешён только встроенной заглушке.
Источник: `document_id`, `chunk_id`, `title`, `quote`, необязательные `section`, `page` (целое от 1).
Проверку существования фрагментов и подтверждённости утверждений обеспечивает ядро участника №2. Backend проверяет схему и наличие источников для answered, но не объявляет это доказательством достоверности.
Максимум ответа — 10 000 символов. Все дополнительные поля запроса запрещены: клиент не задаёт role, model, system, history, key, URL.

## SSE

`POST /api/chats/{id}/messages/stream` с тем же JSON. Ответ `text/event-stream`:

```text
event: result
data: {"request_id":"...","replayed":false,"result":{...}}

event: done
data: {}
```

Это доставка готового проверенного результата. Живой поток токенов не реализован. Ошибки до начала SSE возвращаются обычным JSON и HTTP-статусом. Используйте fetch, EventSource не поддерживает POST.

## Ошибки

Единый ответ прикладной ошибки: `{ "error": "code" }`, без текста контактов, модели, ключей и внутренних исключений.
401 session_required; 404 chat_not_found; 409 chat_archived/chat_busy/chat_not_archived/chat_limit_reached/idempotency_conflict/stale_retry;
413 body_too_large; 415 json_required; 422 invalid_request; 502 core_unavailable_or_invalid; 504 core_timeout.
502/504 не означают отсутствие сведений в базе. Failed-сообщение сохранено без поддельного ответа; его можно повторить с прежним request_id, пока не отправлено более новое.

`GET /health` → `{ "status": "ok", "core_mode": "stub" }`: проверка SQLite и конфигурации, **не готовность реального RAG**.
Заявки, факты клиента и просмотр исходных документов пока не реализованы и не представлены рабочими API.
