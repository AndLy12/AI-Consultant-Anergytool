/**
 * Экран проверки для сотрудника: все диалоги и все заявки.
 *
 * Доступ выдаёт backend по признаку is_staff (команда grant-staff у роли №1).
 * Экран предназначен для проверяющего, а не для публикации: данных других
 * пользователей здесь больше, чем в обычном интерфейсе.
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { Application, Dialog, DialogWithMessages } from '../api/types'
import { describeError } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import MessageBubble from './MessageBubble'

type Props = {
  onBack: () => void
  onSessionExpired: () => void
}

type Tab = 'dialogs' | 'applications'

export default function StaffScreen({ onBack, onSessionExpired }: Props) {
  const [tab, setTab] = useState<Tab>('dialogs')
  const [dialogs, setDialogs] = useState<Dialog[]>([])
  const [applications, setApplications] = useState<Application[]>([])
  const [opened, setOpened] = useState<DialogWithMessages | null>(null)
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
        const [allDialogs, allApplications] = await Promise.all([
          api.staffDialogs(),
          api.staffApplications(),
        ])
        if (cancelled) return
        setDialogs([...allDialogs].reverse())
        setApplications(allApplications)
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
    try {
      setOpened(await api.staffDialog(id))
    } catch (caught) {
      handleFailure(caught)
    }
  }

  if (opened) {
    return (
      <div className="screen">
        <div className="screen-head">
          <button type="button" className="button button-ghost" onClick={() => setOpened(null)}>
            ← К списку
          </button>
          <h1 className="screen-title">Диалог {opened.id}</h1>
        </div>
        <p className="screen-note">
          Пользователь {opened.user_id} · {formatDateTime(opened.created_at)} ·{' '}
          {opened.status === 'active' ? 'активный' : 'архив'}
        </p>
        <div className="feed feed-readonly">
          {opened.messages.map((message) => (
            <MessageBubble key={message.id} message={message} debug />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="screen">
      <div className="screen-head">
        <button type="button" className="button button-ghost" onClick={onBack}>← К чату</button>
        <h1 className="screen-title">Проверка обращений</h1>
      </div>

      <div className="tabs">
        <button
          type="button"
          className={tab === 'dialogs' ? 'tab tab-active' : 'tab'}
          onClick={() => setTab('dialogs')}
        >
          Диалоги ({dialogs.length})
        </button>
        <button
          type="button"
          className={tab === 'applications' ? 'tab tab-active' : 'tab'}
          onClick={() => setTab('applications')}
        >
          Заявки ({applications.length})
        </button>
      </div>

      {error ? <p className="banner banner-error" role="alert">{error}</p> : null}
      {loading ? <p className="empty" role="status">Загружаем данные…</p> : null}

      {tab === 'dialogs' ? (
        <ul className="archive-list">
          {dialogs.map((dialog) => (
            <li key={dialog.id}>
              <button type="button" className="archive-item" onClick={() => void open(dialog.id)}>
                <span className="archive-date">{formatDateTime(dialog.created_at)}</span>
                <span className={dialog.status === 'active' ? 'chip chip-active' : 'chip'}>
                  {dialog.status === 'active' ? 'Активный' : 'Архив'}
                </span>
                <span className="archive-preview">пользователь {dialog.user_id}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="staff-applications">
          {applications.length === 0 && !loading ? <li className="empty">Заявок пока нет.</li> : null}
          {applications.map((application) => (
            <li key={application.id} className="staff-application">
              <div className="staff-application-head">
                <span className={application.status === 'created' ? 'chip chip-done' : 'chip'}>
                  {application.status === 'created' ? 'Создана' : 'Черновик'}
                </span>
                <span className="staff-application-id">{application.id}</span>
                <span>{formatDateTime(application.created_at)}</span>
              </div>
              <dl className="lead-card">
                <div><dt>ФИО</dt><dd>{application.full_name}</dd></div>
                <div><dt>Телефон</dt><dd>{application.phone}</dd></div>
                <div><dt>Компания</dt><dd>{application.company}</dd></div>
                <div><dt>Резюме</dt><dd className="lead-card-summary">{application.summary}</dd></div>
                <div><dt>Диалог</dt><dd>{application.dialog_id}</dd></div>
              </dl>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
