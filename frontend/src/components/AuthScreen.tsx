/**
 * Экран входа и регистрации по макету Figma (кадры 15:68, 15:189, 15:210, 15:227).
 *
 * Один экран с двумя режимами: «вход» и «регистрация». Переключает режим
 * вторая кнопка («Зарегистрируйтесь» / «Войдите»); введённая почта при этом
 * сохраняется, пароль очищается — у входа и регистрации пароли разные по смыслу.
 *
 * Backend не подтверждает почту и не восстанавливает пароль — согласованное
 * ограничение прототипа (docs/DECISIONS.md п.5), поэтому ссылки «забыли пароль» нет.
 */

import { useState } from 'react'
import { api } from '../api/client'
import type { AuthResult } from '../api/types'
import { describeError } from '../lib/errors'
import eyeIcon from '../assets/icons/eye.svg'
import eyeOffIcon from '../assets/icons/eye-off.svg'

type Props = {
  onSignedIn: (result: AuthResult) => void
  stub: boolean
  /** Backend сообщил core_demo: true — предупреждаем ещё до входа. */
  demo: boolean
}

/** Минимальная длина пароля — как у backend (10–128 символов). */
const PASSWORD_MIN = 10
const PASSWORD_MAX = 128

/**
 * Грубая проверка формата почты только для того, чтобы покрасить кнопку.
 * Настоящую проверку делает backend: если он не примет адрес, ошибка
 * покажется под полями.
 */
function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim())
}

export default function AuthScreen({ onSignedIn, stub, demo }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const isLogin = mode === 'login'
  // Вход: кнопка «Далее» активна, когда заполнены оба поля.
  // Регистрация: когда почта похожа на адрес и пароль не короче 10 символов.
  const ready = isLogin
    ? email.trim() !== '' && password !== ''
    : looksLikeEmail(email) && password.length >= PASSWORD_MIN

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    // Enter в поле отправляет форму; повторный Enter во время запроса и
    // Enter при незаполненной форме ничего не делают.
    if (busy || !ready) return
    setBusy(true)
    setError('')
    try {
      const result = isLogin
        ? await api.login(email.trim(), password)
        : await api.register(email.trim(), password)
      onSignedIn(result)
    } catch (caught) {
      setError(describeError(caught))
    } finally {
      setBusy(false)
    }
  }

  function switchMode() {
    setMode(isLogin ? 'register' : 'login')
    setPassword('')
    setShowPassword(false)
    setError('')
  }

  // По макету вторая кнопка на входе красная, пока «Далее» серая (15:68),
  // и серая, когда форма заполнена (15:189). На регистрации она серая всегда
  // (15:210, 15:227). Серая — только внешний вид: кнопка всегда нажимается.
  const secondaryMuted = isLogin ? ready : true

  return (
    <div className="auth">
      <div className={isLogin ? 'auth-card' : 'auth-card auth-card-register'}>
        <h1 className="auth-title">
          {isLogin ? (
            'Войдите по электронной почте'
          ) : (
            <>
              Зарегистрируйтесь
              <br />
              по электронной почте
            </>
          )}
        </h1>

        <form className="auth-form" onSubmit={submit} noValidate>
          <input
            className="auth-input"
            type="email"
            aria-label="Электронная почта"
            placeholder="Введите вашу почту"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="username"
            required
          />

          <div className="auth-password">
            <input
              className="auth-input auth-input-password"
              type={showPassword ? 'text' : 'password'}
              aria-label="Пароль"
              aria-describedby={isLogin ? undefined : 'auth-password-hint'}
              placeholder={isLogin ? 'Введите ваш пароль' : 'Придумайте пароль'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={isLogin ? 'current-password' : 'new-password'}
              maxLength={PASSWORD_MAX}
              required
            />
            {/* Подпись без слова «пароль»: иначе поиск поля по подписи «Пароль»
                находил бы и эту кнопку. Состояние передаёт aria-pressed. */}
            <button
              type="button"
              className="auth-eye"
              aria-label="Показать символы"
              aria-pressed={showPassword}
              onClick={() => setShowPassword((value) => !value)}
            >
              <img src={showPassword ? eyeIcon : eyeOffIcon} alt="" width={24} height={24} />
            </button>
          </div>

          {!isLogin && (
            <p className="auth-hint" id="auth-password-hint">Не менее 10 символов</p>
          )}

          {error && <p className="auth-error" role="alert">{error}</p>}

          {/* Видимая надпись — «ДАЛЕЕ», как в макете; для экранного диктора
              добавлено, что произойдёт: войти или создать аккаунт. */}
          <button
            type="submit"
            className="auth-button auth-button-primary"
            aria-label={isLogin ? 'Далее: войти' : 'Далее: создать аккаунт'}
            disabled={!ready || busy}
          >
            {busy ? 'Подождите…' : 'Далее'}
          </button>
        </form>

        <div className="auth-divider" aria-hidden="true">
          <span className="auth-divider-line" />
          <span className="auth-divider-text">или</span>
          <span className="auth-divider-line" />
        </div>

        <button
          type="button"
          className={secondaryMuted ? 'auth-button auth-button-muted' : 'auth-button auth-button-primary'}
          onClick={switchMode}
          disabled={busy}
        >
          {isLogin ? 'Зарегистрируйтесь' : 'Войдите'}
        </button>

        {/* Плашек режимов нет в макете; они обязательны по требованиям,
            поэтому стоят мелко в нижнем отступе карточки и не сдвигают макет. */}
        {(stub || demo) && (
          <div className="auth-modes">
            {stub && (
              <p className="badge badge-test">
                Тестовый режим: ядро консультанта ещё не подключено, ответы будут заглушкой.
              </p>
            )}
            {demo && (
              <p className="badge badge-test badge-demo">
                Демо-режим: ответы строятся на вымышленных документах, это не настоящая консультация.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
