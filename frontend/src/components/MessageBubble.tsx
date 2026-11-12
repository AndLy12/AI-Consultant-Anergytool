/**
 * Одно сообщение в ленте.
 *
 * Содержимое выводится только как текст (React экранирует его сам): разметку из
 * сообщений исполнять нельзя. Состояния clarify, out_of_scope и нехватки данных
 * показываем отдельными подписями — это не ошибки, а нормальное поведение бота.
 */

import type { Message } from '../api/types'
import { formatTime } from '../lib/format'
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
  const meta = message.result_meta
  const label = (meta.action ? ACTION_LABEL[meta.action] : '')
    || (meta.core_status ? CORE_STATUS_LABEL[meta.core_status] : '')
  const missing = meta.missing_information ?? []

  if (message.role === 'user') {
    return (
      <article className="row row-user">
        <div className="bubble bubble-user">
          <p className="bubble-text">{message.content}</p>
          <p className="bubble-time">{formatTime(message.created_at)}</p>
        </div>
      </article>
    )
  }

  return (
    <article className="row row-bot">
      <div className="avatar" aria-hidden="true">E</div>
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

        <SourcesBlock sources={message.sources} />
        {debug ? <DebugDetails message={message} /> : null}
        <p className="bubble-time">{formatTime(message.created_at)}</p>
      </div>
    </article>
  )
}
