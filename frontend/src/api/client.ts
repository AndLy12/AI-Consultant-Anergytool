/**
 * Единственная точка обращения к backend.
 *
 * Правила backend, от которых зависит этот модуль:
 * - аутентификация по HttpOnly-cookie, поэтому все запросы идут с credentials;
 * - любой изменяющий запрос обязан нести X-CSRF-Token из register/login/me;
 * - ошибки приходят в формате FastAPI: {"detail": "текст"} либо массив для 422.
 */

import type {
  Application,
  ApplicationInput,
  AuthResult,
  Dialog,
  DialogWithMessages,
  Health,
  Message,
  ResultMeta,
} from './types'

/** Ошибка backend с сохранённым HTTP-кодом: по нему интерфейс выбирает поведение. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, message: string, detail: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** Сеть недоступна или запрос не дошёл: ответ сервера неизвестен. */
export class NetworkError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'NetworkError'
  }
}

/**
 * Ответ пришёл, но это не JSON: например, HTML-страница ошибки прокси (502/504)
 * или страница-заглушка вместо backend. Разобрать его нельзя, и неизвестно,
 * успел ли backend выполнить запрос, поэтому это техническая ошибка.
 */
export class BadResponseError extends Error {
  readonly status: number

  constructor(status: number) {
    super('Сервер прислал ответ в неожиданном формате')
    this.name = 'BadResponseError'
    this.status = status
  }
}

let csrfToken = ''

export function setCsrfToken(token: string): void {
  csrfToken = token
}

/** Достаём человекочитаемый текст из detail FastAPI, не теряя массив ошибок 422. */
function readDetail(payload: unknown): { message: string; detail: unknown } {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail: unknown }).detail
    if (typeof detail === 'string') return { message: detail, detail }
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: string } | undefined
      return { message: first?.msg ?? 'Проверьте заполненные поля', detail }
    }
  }
  return { message: 'Неизвестная ошибка', detail: payload }
}

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      method,
      // Cookie сессии выставлена backend и ходит только на свой origin.
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    // fetch падает только при сетевой ошибке: сервер мог и выполнить запрос.
    throw new NetworkError('Нет связи с сервером')
  }

  if (response.status === 204) return null as T
  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      // Раньше здесь вылетала ошибка разбора с английским текстом браузера.
      throw new BadResponseError(response.status)
    }
  }

  if (!response.ok) {
    const { message, detail } = readDetail(payload)
    throw new ApiError(response.status, message, detail)
  }
  return payload as T
}

/**
 * Ответ POST /messages приходит «плоским»: поля result_meta продублированы наверху.
 * В истории диалога те же поля лежат внутри result_meta. Приводим к одному виду,
 * чтобы остальной интерфейс не знал про эту разницу.
 */
function normalizeMessage(raw: Message & ResultMeta): Message {
  const meta: ResultMeta = raw.result_meta && Object.keys(raw.result_meta).length > 0
    ? raw.result_meta
    : {
        action: raw.action,
        summary_draft: raw.summary_draft,
        core_status: raw.core_status,
        missing_information: raw.missing_information,
        warnings: raw.warnings,
        retrieved_candidates: raw.retrieved_candidates,
        context_chunks: raw.context_chunks,
        timings: raw.timings,
      }
  return {
    id: raw.id,
    dialog_id: raw.dialog_id,
    request_id: raw.request_id,
    role: raw.role,
    content: raw.content,
    status: raw.status,
    sources: raw.sources ?? [],
    result_meta: meta,
    elapsed_ms: raw.elapsed_ms ?? 0,
    created_at: raw.created_at,
  }
}

export const api = {
  health: () => request<Health>('/health'),

  register: (email: string, password: string) =>
    request<AuthResult>('/api/auth/register', 'POST', { email, password }),

  login: (email: string, password: string) =>
    request<AuthResult>('/api/auth/login', 'POST', { email, password }),

  me: () => request<AuthResult>('/api/auth/me'),

  logout: () => request<null>('/api/auth/logout', 'POST'),

  createDialog: () => request<Dialog>('/api/dialogs', 'POST'),

  listDialogs: () => request<Dialog[]>('/api/dialogs'),

  async getDialog(id: string): Promise<DialogWithMessages> {
    const dialog = await request<DialogWithMessages>(`/api/dialogs/${id}`)
    return { ...dialog, messages: dialog.messages.map((m) => normalizeMessage(m as Message & ResultMeta)) }
  },

  closeDialog: (id: string) => request<null>(`/api/dialogs/${id}/close`, 'POST'),

  async sendMessage(dialogId: string, requestId: string, content: string): Promise<Message> {
    const raw = await request<Message & ResultMeta>(`/api/dialogs/${dialogId}/messages`, 'POST', {
      request_id: requestId,
      content,
    })
    return normalizeMessage(raw)
  },

  getApplication: (dialogId: string) =>
    request<Application>(`/api/dialogs/${dialogId}/application`),

  saveApplication: (dialogId: string, input: ApplicationInput) =>
    request<Application>(`/api/dialogs/${dialogId}/application`, 'PUT', input),

  confirmApplication: (dialogId: string, version: number) =>
    request<Application>(`/api/dialogs/${dialogId}/application/confirm`, 'POST', {
      version,
      consent: true,
    }),

  staffDialogs: () => request<Dialog[]>('/api/staff/dialogs'),

  staffDialog: (id: string) => request<DialogWithMessages>(`/api/staff/dialogs/${id}`),

  staffApplications: () => request<Application[]>('/api/staff/applications'),
}
