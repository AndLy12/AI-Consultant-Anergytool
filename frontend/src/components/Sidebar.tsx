/**
 * Левая колонка приложения по макету Figma (кадры 9:2, 18:429, 19:488, 20:712).
 *
 * Сверху: «Energytool», «Новый диалог», «Оставить заявку»; ниже — история чатов;
 * внизу — «Выйти из аккаунта» и «Режим отладки». Колонка заменяет прежнюю шапку,
 * поэтому здесь же плашки тестового/демо-режима и вход в экран сотрудника.
 *
 * Сами действия («новый диалог», «заявка») выполняет экран чата: колонка только
 * сообщает о нажатии через onNewDialog/onLead. Так подтверждение нового диалога
 * и создание диалога ради заявки остаются в одном месте с остальной логикой чата.
 *
 * На узком экране колонка выезжает поверх чата по кнопке меню (open/onClose).
 */

import { useEffect, useState } from 'react'
import type { User } from '../api/types'
import { loadDialogRows } from '../lib/dialogRows'
import type { DialogRow } from '../lib/dialogRows'
import messageAddIcon from '../assets/icons/message-add.svg'
import smsIcon from '../assets/icons/sms.svg'
import ModeBadges from './ModeBadges'

type Props = {
  user: User
  /** Диалог, подсвеченный в истории: открытый в чате или в просмотре архива. */
  highlightId: string | null
  /** Меняется, когда список диалогов мог измениться: колонка перечитывает историю. */
  historyVersion: number
  debug: boolean
  onDebug: (value: boolean) => void
  onLogout: () => void
  onNewDialog: () => void
  onLead: () => void
  /** Заголовок «История чатов»: полный список диалогов с датами. */
  onOpenHistory: () => void
  /** Пункт истории: активный диалог открывает чат, остальные — просмотр для чтения. */
  onOpenDialog: (row: DialogRow) => void
  onStaff: () => void
  stub: boolean
  demo: boolean
  /** Колонка раскрыта на узком экране. На широком всегда видна. */
  open: boolean
  onClose: () => void
}

/** Сколько диалогов показываем в колонке; полный список — по «История чатов». */
const SIDEBAR_HISTORY_LIMIT = 20

export default function Sidebar(props: Props) {
  const { user, highlightId, historyVersion, debug, onDebug, onLogout, onNewDialog, onLead } = props
  const { onOpenHistory, onOpenDialog, onStaff, stub, demo, open, onClose } = props
  const [rows, setRows] = useState<DialogRow[]>([])

  useEffect(() => {
    let cancelled = false
    // Ошибка загрузки истории не мешает чату: список просто останется прежним.
    loadDialogRows(SIDEBAR_HISTORY_LIMIT)
      .then((loaded) => {
        if (!cancelled) setRows(loaded.slice(0, SIDEBAR_HISTORY_LIMIT))
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [historyVersion])

  // Esc закрывает выехавшую колонку на телефоне.
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <>
      {open ? <button type="button" className="sidebar-backdrop" aria-label="Закрыть меню" onClick={onClose} /> : null}
      <aside className={open ? 'sidebar sidebar-open' : 'sidebar'} aria-label="Меню">
        <div className="sidebar-brand">
          <span className="brand-name">Energytool</span>
          <ModeBadges stub={stub} demo={demo} />
        </div>

        <nav className="sidebar-menu" aria-label="Действия">
          <button type="button" className="sidebar-action" onClick={onNewDialog}>
            <img src={messageAddIcon} alt="" width={27} height={27} />
            Новый диалог
          </button>
          <button type="button" className="sidebar-action" onClick={onLead}>
            <img src={smsIcon} alt="" width={27} height={27} />
            Оставить заявку
          </button>
        </nav>

        <hr className="sidebar-divider" />

        {/* Заголовок нажимается: открывает полный список с датами (для длинной истории). */}
        <button type="button" className="sidebar-history-title" onClick={onOpenHistory}>
          История чатов
        </button>
        <ul className="sidebar-history">
          {rows.map((row) => (
            <li key={row.dialog.id}>
              <button
                type="button"
                className={row.dialog.id === highlightId ? 'sidebar-chat sidebar-chat-active' : 'sidebar-chat'}
                aria-current={row.dialog.id === highlightId ? 'true' : undefined}
                title={row.preview || undefined}
                onClick={() => onOpenDialog(row)}
              >
                {row.preview || 'Без сообщений'}
              </button>
            </li>
          ))}
        </ul>

        <div className="sidebar-bottom">
          {user.is_staff ? (
            <button type="button" className="sidebar-staff" onClick={onStaff}>
              Проверка обращений
            </button>
          ) : null}
          <button type="button" className="sidebar-button sidebar-button-muted" onClick={onLogout}>
            Выйти из аккаунта
          </button>
          {/* По макету: отладка выключена — кнопка красная (18:429), включена — серая (19:488).
              Состояние для диктора передаёт aria-pressed. */}
          <button
            type="button"
            className={debug ? 'sidebar-button sidebar-button-muted sidebar-debug' : 'sidebar-button sidebar-debug'}
            aria-pressed={debug}
            onClick={() => onDebug(!debug)}
          >
            Режим отладки
          </button>
        </div>
      </aside>
    </>
  )
}
