/**
 * Основной экран: активный диалог, отправка сообщений и путь до заявки.
 *
 * Правила backend, которые определяют поведение экрана:
 * - у аккаунта один активный диалог; POST /api/dialogs закрывает прежний;
 * - на каждое отправленное сообщение создаётся один request_id; повтор с тем же
 *   идентификатором и текстом возвращает сохранённый ответ и не вызывает модель снова;
 * - после сбоя ядра (503) сообщение остаётся failed, и повтор требует НОВОГО request_id;
 * - вторая генерация в том же диалоге отклоняется (409), поэтому отправка блокируется.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { Application, DialogWithMessages, Message } from '../api/types'
import { describeError, isTechnicalFailure, needsNewRequestId } from '../lib/errors'
import Composer from './Composer'
import ConfirmModal from './ConfirmModal'
import LeadPanel from './LeadPanel'
import type { LeadStage } from './LeadPanel'
import MessageBubble from './MessageBubble'
import StartSuggestions from './StartSuggestions'

type Props = {
  debug: boolean
  onSessionExpired: () => void
}

type FailedSend = {
  content: string
  requestId: string
  text: string
  technical: boolean
  /** Нужен ли новый request_id при повторе: после 503 — да, после обрыва сети — нет. */
  fresh: boolean
}

/** Запасной черновик резюме, если ядро его не прислало: только слова самого клиента. */
function buildSummaryFallback(messages: Message[]): string {
  const lines = messages
    .filter((message) => message.role === 'user' && message.status !== 'failed')
    .map((message) => message.content.trim())
  if (lines.length === 0) return ''
  return `Запрос клиента:\n${lines.join('\n')}`.slice(0, 4000)
}

/** Как часто перечитываем диалог, пока backend готовит ответ на сообщение из истории. */
const WAIT_POLL_MS = 1500

/**
 * Неудачная отправка, восстановленная из истории после перезагрузки страницы.
 *
 * Состояние «ошибка + Повторить» живёт в памяти и пропадает при F5, а backend
 * хранит такое сообщение со статусом failed. Если оно последнее в диалоге,
 * снова предлагаем повтор. Прежний request_id backend для неудачного сообщения
 * не принимает, поэтому повтор всегда идёт с новым идентификатором.
 */
function failedFromHistory(messages: Message[]): FailedSend | null {
  const last = messages[messages.length - 1]
  if (!last || last.role !== 'user' || last.status !== 'failed') return null
  return {
    content: last.content,
    requestId: last.request_id,
    text: 'Ответ на это сообщение не получен из-за сбоя сервиса.',
    technical: true,
    fresh: true,
  }
}

export default function ChatScreen({ debug, onSessionExpired }: Props) {
  const [dialog, setDialog] = useState<DialogWithMessages | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const [pending, setPending] = useState<string>('')
  const [failed, setFailed] = useState<FailedSend | null>(null)
  const [card, setCard] = useState<Application | null>(null)
  const [leadStage, setLeadStage] = useState<LeadStage>('hidden')
  const [askNewDialog, setAskNewDialog] = useState(false)
  const [askClose, setAskClose] = useState(false)

  // Предложение заявки не должно появляться снова после отказа пользователя.
  const declinedOffer = useRef<string>('')
  const feedEnd = useRef<HTMLDivElement>(null)

  const handleFailure = useCallback(
    (caught: unknown) => {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired()
        return
      }
      setError(describeError(caught))
    },
    [onSessionExpired],
  )

  /** Карточку читаем отдельно: 404 означает «заявки ещё нет» и ошибкой не является. */
  const loadCard = useCallback(async (dialogId: string) => {
    try {
      setCard(await api.getApplication(dialogId))
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setCard(null)
        return
      }
      throw caught
    }
  }, [])

  // При открытии экрана восстанавливаем активный диалог аккаунта, если он есть.
  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const dialogs = await api.listDialogs()
        const active = [...dialogs].reverse().find((item) => item.status === 'active')
        if (!active) {
          if (!cancelled) setDialog(null)
          return
        }
        const full = await api.getDialog(active.id)
        if (cancelled) return
        setDialog(full)
        // После F5 возвращаем кнопку «Повторить», если последняя отправка не удалась.
        setFailed(failedFromHistory(full.messages))
        await loadCard(active.id)
      } catch (caught) {
        if (!cancelled) handleFailure(caught)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [handleFailure, loadCard])

  // Лента прокручивается к последнему сообщению и к индикатору ожидания.
  useEffect(() => {
    feedEnd.current?.scrollIntoView({ block: 'end' })
  }, [dialog?.messages.length, pending, failed])

  const messages = dialog?.messages ?? []

  // Сообщение из истории ещё обрабатывается: так бывает после F5 во время ожидания.
  // Сам запрос оборвался вместе со страницей, но backend продолжает готовить ответ.
  const lastMessage = messages[messages.length - 1]
  const waiting = !sending && lastMessage?.role === 'user' && lastMessage.status === 'processing'

  const summaryDraft = useMemo(() => {
    const fromCore = [...messages]
      .reverse()
      .find((message) => message.role === 'assistant' && message.result_meta.summary_draft)
    return fromCore?.result_meta.summary_draft || buildSummaryFallback(messages)
  }, [messages])

  /** Показываем предложение заявки, когда ядро отметило действие offer_application. */
  const considerOffer = useCallback(
    (fresh: DialogWithMessages, application: Application | null) => {
      if (application?.status === 'created') return
      const last = [...fresh.messages].reverse().find((message) => message.role === 'assistant')
      if (!last || last.result_meta.action !== 'offer_application') return
      if (declinedOffer.current === last.id) return
      declinedOffer.current = last.id
      setLeadStage('offer')
    },
    [],
  )

  // Пока ждём ответ на сообщение из истории, перечитываем диалог. Остановимся, когда
  // появится ответ или backend отметит сообщение как failed (тогда покажем «Повторить»).
  const dialogId = dialog?.id
  useEffect(() => {
    if (!waiting || !dialogId) return
    let cancelled = false
    const timer = window.setInterval(async () => {
      try {
        const fresh = await api.getDialog(dialogId)
        // Пользователь мог уже открыть другой диалог: устаревший ответ не применяем.
        if (cancelled) return
        setDialog(fresh)
        setFailed(failedFromHistory(fresh.messages))
        considerOffer(fresh, card)
      } catch (caught) {
        if (cancelled) return
        if (caught instanceof ApiError && caught.status === 401) onSessionExpired()
        // Прочие ошибки не показываем: следующая попытка будет через WAIT_POLL_MS.
      }
    }, WAIT_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [waiting, dialogId, card, considerOffer, onSessionExpired])

  async function send(text: string, requestIdOverride?: string) {
    // Второе сообщение backend отклонит, пока не готов ответ на первое.
    if (sending || waiting) return
    setError('')
    setFailed(null)

    let target = dialog
    try {
      if (!target || target.status !== 'active') {
        const created = await api.createDialog()
        target = { ...created, messages: [] }
        setDialog(target)
        setCard(null)
        setLeadStage('hidden')
      }
    } catch (caught) {
      handleFailure(caught)
      return
    }

    const dialogId = target.id
    const requestId = requestIdOverride ?? crypto.randomUUID()
    setPending(text)
    setSending(true)
    try {
      await api.sendMessage(dialogId, requestId, text)
      const fresh = await api.getDialog(dialogId)
      setDialog(fresh)
      setPending('')
      considerOffer(fresh, card)
    } catch (caught) {
      setPending('')
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired()
        return
      }
      setFailed({
        content: text,
        requestId,
        text: describeError(caught),
        technical: isTechnicalFailure(caught),
        fresh: needsNewRequestId(caught),
      })
      // История могла измениться: backend сохраняет неудачное сообщение как failed.
      try {
        setDialog(await api.getDialog(dialogId))
      } catch {
        // Ошибку обновления истории не показываем поверх основной ошибки отправки.
      }
    } finally {
      setSending(false)
    }
  }

  function retry() {
    if (!failed) return
    const requestId = failed.fresh ? crypto.randomUUID() : failed.requestId
    const text = failed.content
    setFailed(null)
    void send(text, requestId)
  }

  async function startNewDialog() {
    setAskNewDialog(false)
    setError('')
    setFailed(null)
    try {
      // Пустой неиспользованный диалог не создаём заново: лишние записи засоряют архив.
      if (dialog && dialog.messages.length === 0 && dialog.status === 'active') {
        setLeadStage('hidden')
        return
      }
      const created = await api.createDialog()
      setDialog({ ...created, messages: [] })
      setCard(null)
      setLeadStage('hidden')
      declinedOffer.current = ''
    } catch (caught) {
      handleFailure(caught)
    }
  }

  async function closeDialog() {
    setAskClose(false)
    if (!dialog) return
    try {
      await api.closeDialog(dialog.id)
      setDialog(await api.getDialog(dialog.id))
    } catch (caught) {
      handleFailure(caught)
    }
  }

  function openLead() {
    if (!dialog) return
    if (card?.status === 'created') setLeadStage('created')
    else if (card) setLeadStage('review')
    else setLeadStage('form')
  }

  if (loading) {
    return <div className="boot" role="status">Загружаем диалог…</div>
  }

  const closed = dialog !== null && dialog.status !== 'active'
  const empty = messages.length === 0 && !pending && !failed

  return (
    <div className="chat">
      <div className="chat-toolbar">
        <button type="button" className="button button-ghost" onClick={() => setAskNewDialog(true)}>
          Новый диалог
        </button>
        {dialog && !closed ? (
          <button type="button" className="button button-ghost" onClick={() => setAskClose(true)}>
            Завершить диалог
          </button>
        ) : null}
        {dialog ? (
          <button type="button" className="button button-outline" onClick={openLead}>
            {card?.status === 'created' ? 'Моя заявка' : 'Оставить заявку'}
          </button>
        ) : null}
      </div>

      {error ? (
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="feed" aria-live="polite" aria-label="Переписка с консультантом">
        {empty ? <StartSuggestions onPick={(text) => void send(text)} disabled={sending || waiting} /> : null}

        {messages.map((message) => (
          <div key={message.id}>
            <MessageBubble message={message} debug={debug} />
            {message.role === 'user' && message.status === 'failed' ? (
              <p className="row-note">Сообщение сохранено, но ответ не получен.</p>
            ) : null}
          </div>
        ))}

        {pending || waiting ? (
          <>
            {/* Текст ожидающего сообщения после F5 уже есть в истории: дублировать не нужно. */}
            {pending ? (
              <article className="row row-user">
                <div className="bubble bubble-user bubble-pending">
                  <p className="bubble-text">{pending}</p>
                  <p className="bubble-time">отправляем…</p>
                </div>
              </article>
            ) : null}
            <article className="row row-bot">
              <div className="avatar" aria-hidden="true">E</div>
              <div className="bubble bubble-bot">
                <p className="typing" aria-label="Консультант готовит ответ">
                  <span />
                  <span />
                  <span />
                </p>
              </div>
            </article>
          </>
        ) : null}

        {failed ? (
          <article className="row row-bot">
            <div className="avatar avatar-error" aria-hidden="true">!</div>
            <div className="bubble bubble-error">
              <p className="bubble-label">
                {failed.technical ? 'Техническая ошибка' : 'Запрос не выполнен'}
              </p>
              <p className="bubble-text">{failed.text}</p>
              <p className="bubble-note">
                {failed.technical
                  ? 'Это сбой сервиса, а не отсутствие информации в базе знаний.'
                  : 'Проверьте данные и повторите отправку.'}
              </p>
              <button type="button" className="button button-primary" onClick={retry} disabled={sending || waiting}>
                Повторить
              </button>
            </div>
          </article>
        ) : null}

        <div ref={feedEnd} />
      </div>

      {dialog && leadStage !== 'hidden' ? (
        <LeadPanel
          dialogId={dialog.id}
          stage={leadStage}
          card={card}
          summaryDraft={summaryDraft}
          onStage={setLeadStage}
          onCard={setCard}
          onSessionExpired={onSessionExpired}
        />
      ) : null}

      <Composer onSend={(text) => void send(text)} disabled={sending || waiting} closed={closed} />

      {askNewDialog ? (
        <ConfirmModal
          title="Начать новый диалог?"
          text="Текущий разговор уйдёт в историю, а консультант забудет его детали. Прежние сообщения останутся доступны в разделе «История»."
          confirmLabel="Начать новый"
          onConfirm={() => void startNewDialog()}
          onCancel={() => setAskNewDialog(false)}
        />
      ) : null}

      {askClose ? (
        <ConfirmModal
          title="Завершить диалог?"
          text="Диалог уйдёт в историю и станет доступен только для чтения. Новый диалог можно начать в любой момент."
          confirmLabel="Завершить"
          onConfirm={() => void closeDialog()}
          onCancel={() => setAskClose(false)}
        />
      ) : null}
    </div>
  )
}
