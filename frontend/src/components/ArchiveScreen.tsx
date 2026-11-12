/**
 * История диалогов пользователя.
 *
 * Просмотр архива не меняет активный диалог: backend по GET /api/dialogs/{id}
 * ничего не переключает, а интерфейс открывает запись только для чтения.
 *
 * В списке диалогов backend не отдаёт название и превью, поэтому первую реплику и
 * число сообщений дочитываем отдельными запросами. Для прототипа объём небольшой;
 * поле preview запрошено у роли №1, чтобы убрать эти дополнительные обращения.
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { Application, Dialog, DialogWithMessages } from '../api/types'
import { describeError } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import MessageBubble from './MessageBubble'

type Props = {
  debug: boolean
  onBack: () => void
  onSessionExpired: () => void
}

type Row = {
  dialog: Dialog
  preview: string
  count: number
}

/** Сколько диалогов дочитываем ради превью, чтобы не делать сотни запросов. */
const PREVIEW_LIMIT = 50

export default function ArchiveScreen({ debug, onBack, onSessionExpired }: Props) {
  const [rows, setRows] = useState<Row[]>([])
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
        const dialogs = await api.listDialogs()
        // Новые сверху: backend отдаёт список от старых к новым.
        const ordered = [...dialogs].reverse()
        const detailed = await Promise.all(
          ordered.slice(0, PREVIEW_LIMIT).map(async (dialog) => {
            try {
              const full = await api.getDialog(dialog.id)
              const first = full.messages.find((message) => message.role === 'user')
              return { dialog, preview: first?.content ?? '', count: full.messages.length }
            } catch {
              return { dialog, preview: '', count: 0 }
            }
          }),
        )
        const rest = ordered.slice(PREVIEW_LIMIT).map((dialog) => ({ dialog, preview: '', count: 0 }))
        if (!cancelled) setRows([...detailed, ...rest])
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

  async function open(id: string) {
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
  }

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
