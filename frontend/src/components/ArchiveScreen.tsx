/**
 * Просмотр прошлого диалога из истории в левой колонке.
 *
 * Отдельной страницы со списком диалогов нет (решение владельца): история и так
 * видна в колонке слева, а экран открывается пунктом истории сразу с нужным
 * диалогом (prop openId). Кнопка «← К чату» возвращает в текущий чат.
 *
 * Просмотр не меняет активный диалог: backend по GET /api/dialogs/{id}
 * ничего не переключает, а здесь нет поля ввода — запись только для чтения.
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { Application, DialogWithMessages } from '../api/types'
import { describeError } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import MessageBubble from './MessageBubble'

type Props = {
  debug: boolean
  /** Диалог, который нужно открыть для чтения (клик по пункту истории в колонке). */
  openId: string
  /** Возврат в текущий чат. */
  onBack: () => void
  /** Сообщает колонке, какой диалог открыт, чтобы подсветить его в истории. */
  onOpened?: (id: string | null) => void
  onSessionExpired: () => void
}

export default function ArchiveScreen({ debug, openId, onBack, onOpened, onSessionExpired }: Props) {
  const [opened, setOpened] = useState<DialogWithMessages | null>(null)
  const [card, setCard] = useState<Application | null>(null)
  const [error, setError] = useState('')

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

  useEffect(() => {
    let cancelled = false
    async function load() {
      setError('')
      try {
        const dialog = await api.getDialog(openId)
        let application: Application | null = null
        try {
          application = await api.getApplication(openId)
        } catch (caught) {
          // 404 — по диалогу просто нет заявки, это не ошибка.
          if (!(caught instanceof ApiError && caught.status === 404)) throw caught
        }
        if (cancelled) return
        setOpened(dialog)
        setCard(application)
      } catch (caught) {
        if (!cancelled) handleFailure(caught)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [openId, handleFailure])

  // Подсветка в колонке: открытый диалог.
  useEffect(() => {
    onOpened?.(opened?.id ?? null)
  }, [opened, onOpened])

  const backButton = (
    <button type="button" className="button button-ghost" onClick={onBack}>
      ← К чату
    </button>
  )

  if (!opened) {
    return (
      <div className="screen">
        <div className="screen-head">{backButton}</div>
        {error ? (
          <p className="banner banner-error" role="alert">{error}</p>
        ) : (
          <p className="empty" role="status">Загружаем диалог…</p>
        )}
      </div>
    )
  }

  return (
    <div className="screen">
      <div className="screen-head">
        {backButton}
        <h1 className="screen-title">Диалог от {formatDateTime(opened.created_at)}</h1>
      </div>
      {/* Решение владельца: в открытом диалоге без плашки статуса и пояснения.
          Поля ввода здесь нет: прошлый диалог доступен только для просмотра. */}

      <div className="feed feed-readonly">
        {opened.messages.length === 0 ? <p className="empty">В диалоге нет сообщений.</p> : null}
        {opened.messages.map((message) => (
          <MessageBubble key={message.id} message={message} debug={debug} />
        ))}
      </div>

      {card ? (
        <section className="lead lead-readonly" aria-label="Заявка по диалогу">
          <h2 className="lead-title">
            {card.status === 'created' ? `Заявка создана, номер ${card.id}` : 'Черновик заявки'}
          </h2>
          <dl className="lead-card">
            <div><dt>ФИО</dt><dd>{card.full_name}</dd></div>
            <div><dt>Телефон</dt><dd>{card.phone}</dd></div>
            <div><dt>Компания</dt><dd>{card.company}</dd></div>
            <div><dt>Резюме</dt><dd className="lead-card-summary">{card.summary}</dd></div>
          </dl>
        </section>
      ) : null}
    </div>
  )
}
