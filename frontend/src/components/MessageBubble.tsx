/**
 * Одно сообщение в ленте по макету Figma (18:429, 19:488).
 *
 * Содержимое выводится только как текст (React экранирует его сам): разметку из
 * сообщений исполнять нельзя. Состояния clarify, out_of_scope и нехватки данных
 * показываем отдельными подписями — это не ошибки, а нормальное поведение бота.
 *
 * Под ответом бота — строка-переключатель «Отладка · Источники»: «Отладка» только
 * при включённом режиме отладки, «Источники» всегда. По макету (19:488) раскрытый
 * блок встаёт между ответом и строкой, а галочка у строки смотрит вверх.
 * При включённом режиме отладки блок раскрыт сразу — как в кадре 19:488.
 */

import { useEffect, useState } from 'react'
import type { Message } from '../api/types'
import chevronIcon from '../assets/icons/chevron-down.svg'
import DebugDetails from './DebugDetails'
import SourcesBlock from './SourcesBlock'

type Props = {
  message: Message
  debug: boolean
}

/** Подпись над ответом для действий, которые пользователю стоит понимать. */
const ACTION_LABEL: Record<string, string> = {
  clarify: 'Нужны уточнения',
  out_of_scope: 'Вне темы консультации',
  offer_application: 'Предложение оставить заявку',
}

const CORE_STATUS_LABEL: Record<string, string> = {
  clarification_needed: 'Нужны уточнения',
  insufficient_data: 'В базе знаний нет нужных сведений',
  out_of_scope: 'Вне темы консультации',
}

export default function MessageBubble({ message, debug }: Props) {
  const [open, setOpen] = useState(debug)

  // Переключение режима отладки раскрывает или сворачивает все ответы сразу.
  useEffect(() => {
    setOpen(debug)
  }, [debug])

  if (message.role === 'user') {
    return (
      <article className="row row-user">
        <div className="bubble bubble-user">
          <p className="bubble-text">{message.content}</p>
        </div>
      </article>
    )
  }

  const meta = message.result_meta
  const label = (meta.action ? ACTION_LABEL[meta.action] : '')
    || (meta.core_status ? CORE_STATUS_LABEL[meta.core_status] : '')
  const missing = meta.missing_information ?? []
  const detailsId = `details-${message.id}`

  return (
    <article className="row row-bot">
      <div className="bubble bubble-bot">
        {label ? <p className="bubble-label">{label}</p> : null}
        <p className="bubble-text">{message.content}</p>

        {missing.length > 0 ? (
          <div className="missing">
            <p className="missing-title">Чтобы ответить точнее, не хватает:</p>
            <ul>
              {missing.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      {open ? (
        <div className="answer-details" id={detailsId}>
          {debug ? <DebugDetails message={message} /> : null}
          <SourcesBlock sources={message.sources} />
        </div>
      ) : null}

      <button
        type="button"
        className={open ? 'answer-toggle answer-toggle-open' : 'answer-toggle'}
        aria-expanded={open}
        aria-controls={open ? detailsId : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        {debug ? (
          <>
            Отладка <b aria-hidden="true">·</b> Источники
          </>
        ) : (
          'Источники'
        )}
        <img src={chevronIcon} alt="" width={9} height={4} />
      </button>
    </article>
  )
}
