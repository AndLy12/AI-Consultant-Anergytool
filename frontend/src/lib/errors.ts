/**
 * Перевод ответов backend в понятный пользователю текст.
 *
 * Отдельно различаем техническую недоступность (503, сеть) и обычные конфликты
 * сценария: по требованиям кейса сбой API не должен выглядеть как «нет данных в базе».
 */

import { ApiError, BadResponseError, NetworkError } from '../api/client'
import { FIELD_LABEL, FIELD_ORDER } from './validation'
import type { FieldErrors, FieldName } from './validation'

/** Тексты backend приходят на английском; сопоставляем их с объяснением на русском. */
const MESSAGES: Record<string, string> = {
  'Authentication required': 'Сессия закончилась. Войдите снова.',
  'CSRF token required': 'Сессия устарела. Обновите страницу и повторите.',
  'Origin rejected': 'Запрос отклонён по настройкам безопасности сервера.',
  'Invalid email or password': 'Неверная почта или пароль.',
  'Registration unavailable for this email': 'Такая почта уже зарегистрирована. Попробуйте войти.',
  'Too many attempts; retry in a minute': 'Слишком много попыток входа. Подождите минуту.',
  'Dialog not found': 'Диалог не найден.',
  'Start a new dialog to continue': 'Этот диалог закрыт. Начните новый диалог.',
  'Another message is processing': 'Предыдущее сообщение ещё обрабатывается. Подождите ответ.',
  'Dialog changed; retry': 'Диалог изменился в другой вкладке. Повторите отправку.',
  'Request ID already used for another message': 'Повтор с другим текстом. Отправьте сообщение заново.',
  'Request is processing or failed; use a new request ID after failure':
    'Предыдущая попытка не завершилась. Отправьте сообщение заново.',
  'Consultant temporarily unavailable; your message was saved':
    'Консультант временно недоступен. Ваше сообщение сохранено, попробуйте повторить.',
  'Application not found': 'Карточка заявки ещё не создана.',
  'Application already confirmed': 'Заявка уже создана, изменить её нельзя.',
  'Card changed; review the latest version': 'Карточка изменилась. Проверьте данные и подтвердите снова.',
  'Prepare the confirmation card first': 'Сначала заполните карточку заявки.',
  'Explicit confirmation required': 'Нужно отметить согласие перед созданием заявки.',
  'Staff access required': 'Раздел доступен только сотрудникам.',
}

export function describeError(error: unknown): string {
  if (error instanceof NetworkError) {
    return 'Нет связи с сервером. Проверьте, запущен ли backend, и повторите.'
  }
  if (error instanceof BadResponseError) {
    // Содержимое такого ответа (HTML прокси) пользователю не показываем.
    return 'Техническая ошибка, попробуйте ещё раз.'
  }
  if (error instanceof ApiError) {
    const known = MESSAGES[error.message]
    if (known) return known
    if (error.status === 422) {
      // Разбор по полям делает fieldErrors(); сюда попадают только случаи,
      // которые не удалось привязать к конкретному полю формы.
      const byField = fieldErrors(error)
      const first = FIELD_ORDER.map((name) => byField[name]).find(Boolean)
      if (first) return first
      return describeAuthValidation(error) ?? 'Проверьте заполненные поля.'
    }
    if (error.status >= 500) return 'Сервер временно недоступен. Повторите попытку.'
    return error.message
  }
  if (error instanceof Error) return error.message
  return 'Неизвестная ошибка'
}

/** Элемент массива detail в ответе 422 FastAPI. */
type ValidationItem = {
  type?: string
  loc?: unknown[]
  msg?: string
  ctx?: { min_length?: number; max_length?: number }
}

/** Понятный текст для типовых отказов pydantic; формулировки те же, что в форме. */
function describeValidationItem(field: FieldName, item: ValidationItem): string {
  const limit = item.ctx ?? {}
  if (item.type === 'missing') return 'Заполните поле'
  if (item.type === 'string_too_short') {
    return limit.min_length === 1
      ? 'Поле не может быть пустым'
      : `Слишком короткое значение: не меньше ${limit.min_length} символов`
  }
  if (item.type === 'string_too_long') {
    return `Слишком длинный текст: не больше ${(limit.max_length ?? 0).toLocaleString('ru-RU')} символов`
  }
  if (field === 'phone') {
    return 'Проверьте номер: допустимы цифры, пробелы, скобки, дефис и знак + в начале, всего от 7 до 15 цифр'
  }
  return `Проверьте поле «${FIELD_LABEL[field]}»`
}

/** Поля экрана входа у формы заявки отсутствуют, поэтому разбираем их отдельно. */
function describeAuthValidation(error: ApiError): string | undefined {
  if (!Array.isArray(error.detail)) return undefined
  for (const raw of error.detail as ValidationItem[]) {
    const field = raw.loc?.find((part) => part === 'email' || part === 'password')
    if (field === 'email') return 'Проверьте адрес почты: нужен вид имя@домен.'
    if (field === 'password') {
      if (raw.type === 'string_too_short') return 'Пароль должен быть не короче 10 символов.'
      if (raw.type === 'string_too_long') return 'Пароль должен быть не длиннее 128 символов.'
      return 'Проверьте пароль.'
    }
  }
  return undefined
}

/**
 * Раскладывает ответ 422 по полям формы.
 *
 * FastAPI присылает массив, где loc выглядит как ["body", "phone"]. Берём из него
 * имя поля; записи, не относящиеся к известным полям, пропускаем — их покажет
 * общий текст ошибки.
 */
export function fieldErrors(error: unknown): FieldErrors {
  const result: FieldErrors = {}
  if (!(error instanceof ApiError) || !Array.isArray(error.detail)) return result
  for (const raw of error.detail as ValidationItem[]) {
    const name = raw.loc?.find(
      (part): part is FieldName => typeof part === 'string' && part in FIELD_LABEL,
    )
    if (!name || result[name]) continue
    result[name] = describeValidationItem(name, raw)
  }
  return result
}

/**
 * Техническим сбоем считаем 503 и сетевую ошибку. Именно для них интерфейс
 * показывает «повторить», а не сообщение об отсутствии информации в базе.
 */
export function isTechnicalFailure(error: unknown): boolean {
  if (error instanceof NetworkError || error instanceof BadResponseError) return true
  return error instanceof ApiError && error.status >= 500
}

/**
 * После 503 сообщение сохранено как failed, и backend требует НОВЫЙ request_id.
 * После сетевой ошибки результат запроса неизвестен, поэтому повтор идёт с ТЕМ ЖЕ
 * идентификатором: так backend вернёт уже сохранённый ответ и не вызовет модель дважды.
 * Ответ не в формате JSON (например, HTML прокси) — тот же случай: до backend запрос
 * мог дойти, а результат неизвестен. Если backend всё же отметил сообщение как failed,
 * он ответит 409, и следующий повтор уже пойдёт с новым идентификатором.
 */
export function needsNewRequestId(error: unknown): boolean {
  return !(error instanceof NetworkError || error instanceof BadResponseError)
}
