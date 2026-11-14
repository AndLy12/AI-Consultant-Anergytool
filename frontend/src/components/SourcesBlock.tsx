/**
 * Свёрнутый список источников под ответом консультанта.
 *
 * Показываем то, на что ответ действительно ссылается: это требование кейса
 * о проверяемости. Текст фрагмента выводится как обычный текст, без разметки.
 */

import type { Source } from '../api/types'

export default function SourcesBlock({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return null

  return (
    <details className="sources">
      <summary>
        Источники из базы знаний: {sources.length}
      </summary>
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
    </details>
  )
}
