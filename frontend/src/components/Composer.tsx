/**
 * Поле ввода сообщения по макету Figma (кадр 9:2, слои 9:29 и 11:146).
 *
 * Кнопка блокируется на время запроса: backend отклоняет вторую генерацию
 * в том же диалоге (409), а двойной клик не должен создавать лишних обращений.
 * Кнопка — только стрелка, поэтому её название для диктора и тестов задано
 * через aria-label «Отправить».
 */

import { useEffect, useRef, useState } from 'react'
import arrowIcon from '../assets/icons/arrow-up.svg'

type Props = {
  onSend: (text: string) => void
  disabled: boolean
  /** Диалог закрыт: писать нельзя, предлагаем начать новый. */
  closed: boolean
}

const MAX_LENGTH = 10000
/** Поле растёт под текст до этой высоты, дальше прокручивается. */
const MAX_HEIGHT = 180

export default function Composer({ onSend, disabled, closed }: Props) {
  const [text, setText] = useState('')
  const area = useRef<HTMLTextAreaElement>(null)

  // Поле растёт под текст, но не бесконечно: длинные запросы прокручиваются.
  // Высота задаётся через CSSOM (style.height), это не нарушает CSP style-src 'self'.
  useEffect(() => {
    const node = area.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = String(Math.min(node.scrollHeight, MAX_HEIGHT)) + 'px'
  }, [text])

  function send() {
    const value = text.trim()
    if (!value || disabled) return
    onSend(value)
    setText('')
  }

  function submit(event: React.FormEvent) {
    event.preventDefault()
    send()
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter отправляет, Shift+Enter переносит строку — привычное поведение чата.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      send()
    }
  }

  if (closed) {
    return (
      <div className="composer composer-closed">
        <p>Диалог закрыт и доступен только для чтения. Начните новый диалог, чтобы продолжить.</p>
      </div>
    )
  }

  return (
    <form className="composer" onSubmit={submit}>
      <label className="visually-hidden" htmlFor="composer-input">
        Сообщение консультанту
      </label>
      <textarea
        id="composer-input"
        ref={area}
        className="composer-input"
        value={text}
        onChange={(event) => setText(event.target.value.slice(0, MAX_LENGTH))}
        onKeyDown={onKeyDown}
        placeholder="Спросите что-нибудь...."
        rows={1}
        maxLength={MAX_LENGTH}
        disabled={disabled}
      />
      {/* По макету кнопка красная и при пустом поле; нажатие без текста ничего не делает.
          Во время запроса кнопка неактивна и бледнеет. */}
      <button
        type="submit"
        className={disabled ? 'composer-send composer-send-busy' : 'composer-send'}
        aria-label="Отправить"
        disabled={disabled || text.trim().length === 0}
      >
        <img src={arrowIcon} alt="" width={32} height={32} />
      </button>
    </form>
  )
}
