/**
 * Перевод ответов backend в понятный пользователю текст.
 *
 * Отдельно различаем техническую недоступность (503, сеть) и обычные конфликты
 * сценария: по требованиям кейса сбой API не должен выглядеть как «нет данных в базе».
 */

import { ApiError, NetworkError } from '../api/client'

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
  if (error instanceof ApiError) {
    const known = MESSAGES[error.message]
    if (known) return known
    if (error.status === 422) return `Проверьте поля: ${error.message}`
    if (error.status >= 500) return 'Сервер временно недоступен. Повторите попытку.'
    return error.message
  }
  if (error instanceof Error) return error.message
  return 'Неизвестная ошибка'
}

/**
 * Техническим сбоем считаем 503 и сетевую ошибку. Именно для них интерфейс
 * показывает «повторить», а не сообщение об отсутствии информации в базе.
 */
export function isTechnicalFailure(error: unknown): boolean {
  if (error instanceof NetworkError) return true
  return error instanceof ApiError && error.status >= 500
}

/**
 * После 503 сообщение сохранено как failed, и backend требует НОВЫЙ request_id.
 * После сетевой ошибки результат запроса неизвестен, поэтому повтор идёт с ТЕМ ЖЕ
 * идентификатором: так backend вернёт уже сохранённый ответ и не вызовет модель дважды.
 */
export function needsNewRequestId(error: unknown): boolean {
  return !(error instanceof NetworkError)
}
