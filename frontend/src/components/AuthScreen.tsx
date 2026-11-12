/**
 * Экран входа и регистрации.
 *
 * Backend не подтверждает почту и не восстанавливает пароль — согласованное
 * ограничение прототипа (docs/DECISIONS.md п.5), поэтому ссылки «забыли пароль» нет.
 */

import { useState } from 'react'
import { api } from '../api/client'
import type { AuthResult } from '../api/types'
import { describeError } from '../lib/errors'

type Props = {
  onSignedIn: (result: AuthResult) => void
  stub: boolean
}

export default function AuthScreen({ onSignedIn, stub }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const result = mode === 'login'
        ? await api.login(email.trim(), password)
        : await api.register(email.trim(), password)
      onSignedIn(result)
    } catch (caught) {
      setError(describeError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">Energytool</span>
        </div>
        <h1 className="auth-title">Консультант по энергоресурсам</h1>
        <p className="auth-lead">
          Опишите задачу обычными словами. Консультант уточнит детали, найдёт ответ
          в базе знаний Energytool и при необходимости передаст заявку менеджеру.
        </p>

        {stub && (
          <p className="badge badge-test">
            Тестовый режим: ядро консультанта ещё не подключено, ответы будут заглушкой.
          </p>
        )}

        <div className="tabs" role="tablist" aria-label="Вход или регистрация">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'login'}
            className={mode === 'login' ? 'tab tab-active' : 'tab'}
            onClick={() => { setMode('login'); setError('') }}
          >
            Вход
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'register'}
            className={mode === 'register' ? 'tab tab-active' : 'tab'}
            onClick={() => { setMode('register'); setError('') }}
          >
            Регистрация
          </button>
        </div>

        <form className="form" onSubmit={submit}>
          <label className="field">
            <span className="field-label">Электронная почта</span>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="username"
              required
              placeholder="ivanov@example.test"
            />
          </label>
          <label className="field">
            <span className="field-label">Пароль</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              minLength={10}
              maxLength={128}
              required
              placeholder="не короче 10 символов"
            />
            <span className="field-hint">От 10 до 128 символов.</span>
          </label>

          {error && <p className="form-error" role="alert">{error}</p>}

          <button type="submit" className="button button-primary" disabled={busy}>
            {busy ? 'Подождите…' : mode === 'login' ? 'Войти' : 'Создать аккаунт'}
          </button>
        </form>

        <p className="auth-note">
          Прототип хакатона. Используйте вымышленные данные: почта может быть любой
          несуществующей, например <code>ivanov@example.test</code>.
        </p>
      </div>
    </div>
  )
}
