/**
 * Корневой компонент: вход в аккаунт, выбор экрана и общие признаки среды.
 *
 * Сессия восстанавливается через GET /api/auth/me при загрузке страницы: cookie
 * HttpOnly недоступна из JavaScript, поэтому состояние входа узнаём только у backend.
 *
 * Раскладка по макету Figma: слева колонка (Sidebar), справа белая панель с текущим
 * экраном. Кнопки колонки «Новый диалог» и «Оставить заявку» работают с любого экрана:
 * App переключает на чат и передаёт ему запрос действия (chatAction), а чат выполняет
 * его, когда загрузит активный диалог.
 */

import { useCallback, useEffect, useState } from 'react'
import { api, setCsrfToken } from './api/client'
import type { Health, User } from './api/types'
import ArchiveScreen from './components/ArchiveScreen'
import AuthScreen from './components/AuthScreen'
import ChatScreen from './components/ChatScreen'
import ModeBadges from './components/ModeBadges'
import type { ChatAction } from './components/ChatScreen'
import Sidebar from './components/Sidebar'
import StaffScreen from './components/StaffScreen'
import type { DialogRow } from './lib/dialogRows'

export type Screen = 'chat' | 'archive' | 'staff'

const DEBUG_KEY = 'energytool.debug'

/**
 * Режим отладки запоминаем в браузере: во время приёмки и показа страница
 * перезагружается часто, и включать переключатель каждый раз неудобно.
 * Хранилище может быть недоступно (приватное окно, запрет данных сайта),
 * поэтому любое обращение к нему защищено.
 */
function readDebugPreference(): boolean {
  try {
    return localStorage.getItem(DEBUG_KEY) === '1'
  } catch {
    return false
  }
}

function writeDebugPreference(value: boolean): void {
  try {
    localStorage.setItem(DEBUG_KEY, value ? '1' : '0')
  } catch {
    // Настройка не сохранится, но интерфейс должен продолжить работу.
  }
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checked, setChecked] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [screen, setScreen] = useState<Screen>('chat')
  const [debug, setDebug] = useState(readDebugPreference)
  /** Счётчик заставляет чат перезагрузиться после входа в аккаунт. */
  const [chatKey, setChatKey] = useState(0)
  /** Нажатие в колонке, которое должен выполнить экран чата. */
  const [chatAction, setChatAction] = useState<ChatAction | null>(null)
  /** Диалог, открытый для чтения из пункта истории; undefined — показать список. */
  const [archiveOpenId, setArchiveOpenId] = useState<string | undefined>(undefined)
  const [archiveKey, setArchiveKey] = useState(0)
  /** Подсветка в истории: текущий диалог чата и диалог, открытый в архиве. */
  const [chatDialogId, setChatDialogId] = useState<string | null>(null)
  const [archiveDialogId, setArchiveDialogId] = useState<string | null>(null)
  const [historyVersion, setHistoryVersion] = useState(0)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    // Отсутствие сессии — обычное состояние первого захода, а не ошибка.
    api.me()
      .then((result) => {
        setCsrfToken(result.csrf_token)
        setUser(result.user)
      })
      .catch(() => undefined)
      .finally(() => setChecked(true))
    api.health().then(setHealth).catch(() => undefined)
  }, [])

  const handleDebug = useCallback((value: boolean) => {
    setDebug(value)
    writeDebugPreference(value)
  }, [])

  const handleSignedIn = useCallback((result: { user: User; csrf_token: string }) => {
    setCsrfToken(result.csrf_token)
    setUser(result.user)
    setScreen('chat')
    setChatKey((value) => value + 1)
  }, [])

  const handleLogout = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      setCsrfToken('')
      setUser(null)
      setScreen('chat')
      setMenuOpen(false)
    }
  }, [])

  /** Сессия могла закончиться во время работы: возвращаем экран входа. */
  const handleSessionExpired = useCallback(() => {
    setCsrfToken('')
    setUser(null)
  }, [])

  /**
   * Чат сообщает о смене диалога и о первом сообщении в нём: в обоих случаях
   * в истории появляется новая строка или превью, и колонка перечитывает список.
   */
  const handleDialogState = useCallback((id: string | null) => {
    setChatDialogId(id)
    setHistoryVersion((value) => value + 1)
  }, [])

  const handleActionHandled = useCallback(() => setChatAction(null), [])
  const handleArchiveOpened = useCallback((id: string | null) => setArchiveDialogId(id), [])
  const closeMenu = useCallback(() => setMenuOpen(false), [])

  function requestChatAction(action: ChatAction) {
    setScreen('chat')
    setChatAction(action)
    setMenuOpen(false)
  }

  function openHistoryList() {
    setArchiveOpenId(undefined)
    setArchiveKey((value) => value + 1)
    setScreen('archive')
    setMenuOpen(false)
  }

  function openDialogFromHistory(row: DialogRow) {
    setMenuOpen(false)
    // Активный диалог — это и есть текущий чат: открываем его с полем ввода.
    if (row.dialog.status === 'active') {
      setScreen('chat')
      return
    }
    setArchiveOpenId(row.dialog.id)
    setArchiveKey((value) => value + 1)
    setScreen('archive')
  }

  if (!checked) {
    return (
      <div className="boot" role="status">
        Загружаем консультант…
      </div>
    )
  }

  const stub = health?.core_mode === 'stub'
  // Строгое сравнение: отсутствующее поле (старый backend) не включает плашку.
  const demo = health?.core_demo === true

  if (!user) {
    return <AuthScreen onSignedIn={handleSignedIn} stub={stub} demo={demo} />
  }

  const highlightId = screen === 'chat' ? chatDialogId : screen === 'archive' ? archiveDialogId : null

  return (
    <div className="app">
      <Sidebar
        user={user}
        highlightId={highlightId}
        historyVersion={historyVersion}
        debug={debug}
        onDebug={handleDebug}
        onLogout={handleLogout}
        onNewDialog={() => requestChatAction('new-dialog')}
        onLead={() => requestChatAction('lead')}
        onOpenHistory={openHistoryList}
        onOpenDialog={openDialogFromHistory}
        onStaff={() => {
          setScreen('staff')
          setMenuOpen(false)
        }}
        stub={stub}
        demo={demo}
        open={menuOpen}
        onClose={closeMenu}
      />
      <main className="app-main">
        {/* Полоса с кнопкой меню видна только на узком экране (см. styles.css). */}
        <div className="mobile-bar">
          <button
            type="button"
            className="mobile-menu"
            aria-label="Открыть меню"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
          >
            <span aria-hidden="true" />
          </button>
          <span className="brand-name">Energytool</span>
          <ModeBadges stub={stub} demo={demo} />
        </div>

        {screen === 'chat' && (
          <ChatScreen
            key={chatKey}
            debug={debug}
            action={chatAction}
            onActionHandled={handleActionHandled}
            onDialogState={handleDialogState}
            onSessionExpired={handleSessionExpired}
          />
        )}
        {screen === 'archive' && (
          <ArchiveScreen
            key={archiveKey}
            debug={debug}
            openId={archiveOpenId}
            onBack={() => setScreen('chat')}
            onOpened={handleArchiveOpened}
            onSessionExpired={handleSessionExpired}
          />
        )}
        {screen === 'staff' && user.is_staff && (
          <StaffScreen onBack={() => setScreen('chat')} onSessionExpired={handleSessionExpired} />
        )}
      </main>
    </div>
  )
}
