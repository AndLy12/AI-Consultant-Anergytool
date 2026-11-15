/**
 * Источники ответа консультанта — раскрываются строкой «Источники» под ответом.
 *
 * Показываем то, на что ответ действительно ссылается: это требование кейса
 * о проверяемости. Текст фрагмента выводится как обычный текст, без разметки.
 * Источников может не быть (уточняющий вопрос, вопрос вне темы) — тогда так и пишем.
 */

import type { Source } from '../api/types'

export default function SourcesBlock({ sources }: { sources: Source[] }) {
  return (
    <section className="sources" aria-label="Источники из базы знаний">
      {sources.length === 0 ? (
        <p className="sources-empty">Ответ не ссылается на документы базы знаний.</p>
      ) : (
        <ol className="sources-list">
          {sources.map((source) => (
            <li key={`${source.document_id}:${source.chunk_id}`} className="source">
              <p className="source-title">{source.title}</p>
              <p className="source-meta">
                {source.section && <span>Раздел: {source.section}</span>}
                {typeof source.page === 'number' && <span>Страница: {source.page}</span>}
                <span>Документ: {source.document_id}</span>
              </p>
              <p className="source-excerpt">{source.excerpt}</p>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
