/**
 * История диалогов пользователя.
 *
 * Просмотр архива не меняет активный диалог: backend по GET /api/dialogs/{id}
 * ничего не переключает, а интерфейс открывает запись только для чтения.
 * Первую реплику и число сообщений дочитывает lib/dialogRows (у backend нет превью).
 *
 * Экран открывается двумя путями: заголовком «История чатов» в колонке — со списком,
 * или пунктом истории — сразу с нужным диалогом (prop openId).
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { Application, DialogWithMessages } from '../api/types'
import { loadDialogRows } from '../lib/dialogRows'
import type { DialogRow } from '../lib/dialogRows'
import { describeError } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import MessageBubble from './MessageBubble'

type Props = {
  debug: boolean
  /** Диалог, который нужно сразу открыть для чтения (клик по пункту истории в колонке). */
  openId?: string
  onBack: () => void
  /** Сообщает колонке, какой диалог открыт, чтобы подсветить его в истории. */
  onOpened?: (id: string | null) => void
  onSessionExpired: () => void
}

/** Сколько диалогов дочитываем ради превью, чтобы не делать сотни запросов. */
const PREVIEW_LIMIT = 50

export default function ArchiveScreen({ debug, openId, onBack, onOpened, onSessionExpired }: Props) {
  const [rows, setRows] = useState<DialogRow[]>([])
  const [opened, setOpened] = useState<DialogWithMessages | null>(null)
  const [card, setCard] = useState<Application | null>(null)
  const [loading, setLoading] = useState(true)
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
      try {
        const loaded = await loadDialogRows(PREVIEW_LIMIT)
        if (!cancelled) setRows(loaded)
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
  }, [handleFailure])

  const open = useCallback(
    async (id: string) => {
      setError('')
      try {
        setOpened(await api.getDialog(id))
        try {
          setCard(await api.getApplication(id))
        } catch (caught) {
          if (caught instanceof ApiError && caught.status === 404) setCard(null)
          else throw caught
        }
      } catch (caught) {
        handleFailure(caught)
      }
    },
    [handleFailure],
  )

  // Пункт истории в колонке открывает диалог сразу, минуя список.
  useEffect(() => {
    if (openId) void open(openId)
  }, [openId, open])

  // Подсветка в колонке: открытый диалог или ничего, пока показан список.
  useEffect(() => {
    onOpened?.(opened?.id ?? null)
  }, [opened, onOpened])

  if (opened) {
    return (
      <div className="screen">
        <div className="screen-head">
          <button type="button" className="button button-ghost" onClick={() => { setOpened(null); setCard(null) }}>
            ← К списку
          </button>
          <h1 className="screen-title">Диалог от {formatDateTime(opened.created_at)}</h1>
          <span className={opened.status === 'active' ? 'chip chip-active' : 'chip'}>
            {opened.status === 'active' ? 'Активный' : 'Архив'}
          </span>
        </div>
        <p className="screen-note">Просмотр только для чтения. Активный диалог не меняется.</p>

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

  return (
    <div className="screen">
      <div className="screen-head">
        <button type="button" className="button button-ghost" onClick={onBack}>← К чату</button>
        <h1 className="screen-title">История диалогов</h1>
      </div>
      <p className="screen-note">
        Сохраняются все разговоры, в том числе без заявки. Открытие записи не переключает активный диалог.
      </p>

      {error ? <p className="banner banner-error" role="alert">{error}</p> : null}
      {loading ? <p className="empty" role="status">Загружаем историю…</p> : null}
      {!loading && rows.length === 0 ? <p className="empty">Диалогов пока нет.</p> : null}

      <ul className="archive-list">
        {rows.map(({ dialog, preview, count }) => (
          <li key={dialog.id}>
            <button type="button" className="archive-item" onClick={() => void open(dialog.id)}>
              <span className="archive-date">{formatDateTime(dialog.created_at)}</span>
              <span className={dialog.status === 'active' ? 'chip chip-active' : 'chip'}>
                {dialog.status === 'active' ? 'Активный' : 'Архив'}
              </span>
              <span className="archive-preview">{preview || 'Без сообщений'}</span>
              <span className="archive-count">{count} сообщ.</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
