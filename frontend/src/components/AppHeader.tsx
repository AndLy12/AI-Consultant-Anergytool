/** Шапка приложения: переключение экранов, режим отладки и выход. */

import type { Screen } from '../App'
import type { User } from '../api/types'

type Props = {
  user: User
  screen: Screen
  onScreen: (screen: Screen) => void
  debug: boolean
  onDebug: (value: boolean) => void
  onLogout: () => void
  stub: boolean
}

export default function AppHeader({ user, screen, onScreen, debug, onDebug, onLogout, stub }: Props) {
  return (
    <header className="app-header">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <span className="brand-name">Energytool</span>
        {stub && (
          <span className="badge badge-test" title="Ядро консультанта ещё не подключено">
            тестовый режим
          </span>
        )}
      </div>

      <nav className="nav" aria-label="Разделы">
        <button
          type="button"
          className={screen === 'chat' ? 'nav-item nav-item-active' : 'nav-item'}
          aria-current={screen === 'chat' ? 'page' : undefined}
          onClick={() => onScreen('chat')}
        >
          Чат
        </button>
        <button
          type="button"
          className={screen === 'archive' ? 'nav-item nav-item-active' : 'nav-item'}
          aria-current={screen === 'archive' ? 'page' : undefined}
          onClick={() => onScreen('archive')}
        >
          История
        </button>
        {user.is_staff && (
          <button
            type="button"
            className={screen === 'staff' ? 'nav-item nav-item-active' : 'nav-item'}
            aria-current={screen === 'staff' ? 'page' : undefined}
            onClick={() => onScreen('staff')}
          >
            Проверка
          </button>
        )}
      </nav>

      <div className="header-right">
        <label className="switch">
          <input type="checkbox" checked={debug} onChange={(event) => onDebug(event.target.checked)} />
          <span>Отладка</span>
        </label>
        <span className="header-email" title={user.email}>{user.email}</span>
        <button type="button" className="button button-ghost" onClick={onLogout}>Выйти</button>
      </div>
    </header>
  )
}
