/**
 * Поле ввода сообщения.
 *
 * Кнопка блокируется на время запроса: backend отклоняет вторую генерацию
 * в том же диалоге (409), а двойной клик не должен создавать лишних обращений.
 */

import { useEffect, useRef, useState } from 'react'

type Props = {
  onSend: (text: string) => void
  disabled: boolean
  /** Диалог закрыт: писать нельзя, предлагаем начать новый. */
  closed: boolean
}

const MAX_LENGTH = 10000

export default function Composer({ onSend, disabled, closed }: Props) {
  const [text, setText] = useState('')
  const area = useRef<HTMLTextAreaElement>(null)

  // Поле растёт под текст, но не бесконечно: длинные запросы прокручиваются.
  useEffect(() => {
    const node = area.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = String(Math.min(node.scrollHeight, 180)) + 'px'
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
      <label className="composer-label" htmlFor="composer-input">
        Сообщение консультанту
      </label>
      <textarea
        id="composer-input"
        ref={area}
        className="composer-input"
        value={text}
        onChange={(event) => setText(event.target.value.slice(0, MAX_LENGTH))}
        onKeyDown={onKeyDown}
        placeholder="Опишите задачу…"
        rows={1}
        maxLength={MAX_LENGTH}
        disabled={disabled}
      />
      <button
        type="submit"
        className="button button-primary composer-send"
        disabled={disabled || text.trim().length === 0}
      >
        {disabled ? 'Отправка…' : 'Отправить'}
      </button>
    </form>
  )
}
