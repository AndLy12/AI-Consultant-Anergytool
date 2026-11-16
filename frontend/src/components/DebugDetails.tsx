/**
 * Блок отладки одного ответа по макету Figma (кадр 19:488, слой 19:540).
 *
 * Верхние три строки повторяют макет: «Время ответа», «Документы базы знаний»
 * (процитированные источники) и «Статус ответа». Ниже — технические сведения,
 * которых нет в макете, но которые нужны для приёмки: действие ядра, число
 * источников, идентификатор запроса, предупреждения.
 *
 * Сейчас backend отдаёт время ответа, процитированные источники, core_status,
 * warnings и missing_information. По плану кейса (док. 01 раздел 9) нужны ещё два
 * списка — найденные кандидаты поиска и фрагменты, переданные модели, — и тайминги
 * этапов. Поля запрошены у №1 и №2; блоки ниже появятся сами, как только ответ
 * начнёт их содержать, и до этого ничего не ломают.
 *
 * Скрытые рассуждения модели, ключи и системный промпт не показываем и не запрашиваем.
 */

import type { DebugChunk, Message } from '../api/types'
import { formatElapsed } from '../lib/format'

/** Статус ответа человеческими словами (в макете: «нужны уточнения»). */
const STATUS_TEXT: Record<string, string> = {
  answered: 'ответ дан',
  clarification_needed: 'нужны уточнения',
  insufficient_data: 'в базе знаний нет нужных сведений',
  out_of_scope: 'вне темы консультации',
}

const ACTION_TEXT: Record<string, string> = {
  answer: 'ответ дан',
  clarify: 'нужны уточнения',
  out_of_scope: 'вне темы консультации',
  offer_application: 'предложена заявка',
}

function ChunkList({ title, chunks }: { title: string; chunks: DebugChunk[] }) {
  return (
    <div className="debug-group">
      <p className="debug-group-title">{title}: {chunks.length}</p>
      <ol className="debug-chunks">
        {chunks.map((chunk, index) => (
          <li key={`${chunk.chunk_id ?? index}`}>
            <span className="debug-chunk-title">{chunk.title ?? chunk.document_id ?? 'фрагмент'}</span>
            {typeof chunk.score === 'number' && <span className="debug-score">вес {chunk.score.toFixed(3)}</span>}
            {chunk.excerpt && <span className="debug-chunk-text">{chunk.excerpt}</span>}
          </li>
        ))}
      </ol>
    </div>
  )
}

export default function DebugDetails({ message }: { message: Message }) {
  const meta = message.result_meta
  const candidates = meta.retrieved_candidates ?? []
  const context = meta.context_chunks ?? []
  const timings = meta.timings ?? {}

  // «Ценовые категории и тарифы» (раздел 2), «Пиковые часы потребления» — как в макете.
  const documents = message.sources
    .map((source) => `«${source.title}»${source.section ? ` (раздел ${source.section})` : ''}`)
    .join(', ')
  const status = (meta.core_status && STATUS_TEXT[meta.core_status])
    || (meta.action && ACTION_TEXT[meta.action])
    || 'нет данных'

  return (
    <div className="debug">
      <p className="debug-line"><b>Время ответа:</b> {formatElapsed(message.elapsed_ms)}</p>
      <p className="debug-line"><b>Документы базы знаний:</b> {documents || 'нет'}</p>
      <p className="debug-line"><b>Статус ответа:</b> {status}</p>

      <dl className="debug-grid">
        <div>
          <dt>Действие</dt>
          <dd>{meta.action ?? '—'}</dd>
        </div>
        <div>
          <dt>Статус ядра</dt>
          <dd>{meta.core_status ?? '—'}</dd>
        </div>
        <div>
          <dt>Процитировано источников</dt>
          <dd>{message.sources.length}</dd>
        </div>
        <div>
          <dt>Идентификатор запроса</dt>
          <dd className="debug-id">{message.request_id}</dd>
        </div>
      </dl>

      {Object.keys(timings).length > 0 && (
        <div className="debug-group">
          <p className="debug-group-title">Длительность этапов</p>
          <ul className="debug-plain">
            {Object.entries(timings).map(([stage, value]) => (
              <li key={stage}>{stage}: {formatElapsed(value)}</li>
            ))}
          </ul>
        </div>
      )}

      {candidates.length > 0 && <ChunkList title="Найдено поиском" chunks={candidates} />}
      {context.length > 0 && <ChunkList title="Передано модели" chunks={context} />}

      {candidates.length === 0 && context.length === 0 && (
        <p className="debug-note">
          Backend пока не присылает списки найденных и переданных модели фрагментов —
          поля запрошены у ролей №1 и №2. Показаны процитированные источники и время.
        </p>
      )}

      {(meta.missing_information?.length ?? 0) > 0 && (
        <div className="debug-group">
          <p className="debug-group-title">Не хватает данных</p>
          <ul className="debug-plain">
            {meta.missing_information?.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}

      {(meta.warnings?.length ?? 0) > 0 && (
        <div className="debug-group">
          <p className="debug-group-title">Предупреждения</p>
          <ul className="debug-plain">
            {meta.warnings?.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}
