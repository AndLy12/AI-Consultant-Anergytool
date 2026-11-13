/** Небольшое подтверждение действия, которое нельзя отменить (например, новый диалог). */

import { useEffect, useRef } from 'react'

type Props = {
  title: string
  text: string
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmModal({ title, text, confirmLabel, onConfirm, onCancel }: Props) {
  const confirmButton = useRef<HTMLButtonElement>(null)

  // Фокус сразу на главном действии: окно должно работать с клавиатуры.
  useEffect(() => {
    confirmButton.current?.focus()
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="modal-title" className="modal-title">{title}</h2>
        <p className="modal-text">{text}</p>
        <div className="modal-actions">
          <button type="button" className="button button-ghost" onClick={onCancel}>
            Отмена
          </button>
          <button type="button" className="button button-primary" ref={confirmButton} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
