/**
 * Типы ответов backend роли №1.
 *
 * Источник истины — docs/BACKEND.md и backend/app.py. Поля, которых ещё нет в API
 * (расширенная отладка), помечены как необязательные: панель отладки покажет их
 * автоматически, когда №1 и №2 добавят их в ответ, и не сломается до этого.
 */

export type User = {
  id: string
  email: string
  is_staff: boolean
}

export type AuthResult = {
  user: User
  csrf_token: string
}

export type DialogStatus = 'active' | 'closed'

export type Dialog = {
  id: string
  user_id: string
  status: DialogStatus
  version: number
  created_at: string
}

/** Подсказка сценария от ядра: что интерфейс должен предложить пользователю. */
export type CoreAction = 'answer' | 'clarify' | 'out_of_scope' | 'offer_application'

/** Диагностический статус ядра; дублирует action более подробно. */
export type CoreStatus = 'answered' | 'clarification_needed' | 'insufficient_data' | 'out_of_scope'

export type Source = {
  document_id: string
  chunk_id: string
  title: string
  excerpt: string
  section?: string | null
  page?: number | null
}

/** Фрагмент поиска для расширенной отладки. Backend его пока не присылает. */
export type DebugChunk = {
  document_id?: string
  chunk_id?: string
  title?: string
  excerpt?: string
  score?: number
}

export type ResultMeta = {
  action?: CoreAction
  summary_draft?: string | null
  core_status?: CoreStatus | null
  missing_information?: string[]
  warnings?: string[]
  /** Ожидаемые поля отладки (запрошены у №1/№2, см. frontend/README.md). */
  retrieved_candidates?: DebugChunk[]
  context_chunks?: DebugChunk[]
  timings?: Record<string, number>
}

export type MessageStatus = 'processing' | 'completed' | 'failed'

export type Message = {
  id: string
  dialog_id: string
  request_id: string
  role: 'user' | 'assistant'
  content: string
  status: MessageStatus
  sources: Source[]
  result_meta: ResultMeta
  elapsed_ms: number
  created_at: string
}

export type DialogWithMessages = Dialog & { messages: Message[] }

export type ApplicationStatus = 'draft' | 'created'

export type Application = {
  id: string
  dialog_id: string
  full_name: string
  phone: string
  company: string
  summary: string
  status: ApplicationStatus
  version: number
  consent_at: string | null
  created_at: string
}

export type ApplicationInput = {
  full_name: string
  phone: string
  company: string
  summary: string
}

export type Health = {
  status: string
  /** 'stub' означает, что ядро консультанта ещё не подключено. */
  core_mode: string
}
