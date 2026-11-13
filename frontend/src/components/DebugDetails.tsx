/**
 * Панель отладки одного ответа.
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

  return (
    <div className="debug">
      <p className="debug-title">Данные отладки</p>
      <dl className="debug-grid">
        <div>
          <dt>Время ответа</dt>
          <dd>{formatElapsed(message.elapsed_ms)}</dd>
        </div>
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
