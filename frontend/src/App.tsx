/**
 * Корневой компонент: вход в аккаунт, выбор экрана и общие признаки среды.
 *
 * Сессия восстанавливается через GET /api/auth/me при загрузке страницы: cookie
 * HttpOnly недоступна из JavaScript, поэтому состояние входа узнаём только у backend.
 */

import { useCallback, useEffect, useState } from 'react'
import { api, setCsrfToken } from './api/client'
import type { Health, User } from './api/types'
import AppHeader from './components/AppHeader'
import ArchiveScreen from './components/ArchiveScreen'
import AuthScreen from './components/AuthScreen'
import ChatScreen from './components/ChatScreen'
import StaffScreen from './components/StaffScreen'

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
  /** Счётчик заставляет чат перезагрузиться после возврата из архива. */
  const [chatKey, setChatKey] = useState(0)

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
    }
  }, [])

  /** Сессия могла закончиться во время работы: возвращаем экран входа. */
  const handleSessionExpired = useCallback(() => {
    setCsrfToken('')
    setUser(null)
  }, [])

  if (!checked) {
    return (
      <div className="boot" role="status">
        Загружаем консультант…
      </div>
    )
  }

  if (!user) {
    return <AuthScreen onSignedIn={handleSignedIn} stub={health?.core_mode === 'stub'} />
  }

  return (
    <div className="app">
      <AppHeader
        user={user}
        screen={screen}
        onScreen={setScreen}
        debug={debug}
        onDebug={handleDebug}
        onLogout={handleLogout}
        stub={health?.core_mode === 'stub'}
      />
      <main className="app-main">
        {screen === 'chat' && (
          <ChatScreen key={chatKey} debug={debug} onSessionExpired={handleSessionExpired} />
        )}
        {screen === 'archive' && (
          <ArchiveScreen
            debug={debug}
            onBack={() => setScreen('chat')}
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
