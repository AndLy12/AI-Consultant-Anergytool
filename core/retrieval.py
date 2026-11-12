"""Детерминированный BM25 baseline; индекс общий, пользовательское состояние отсутствует."""

from dataclasses import dataclass
import re
from rank_bm25 import BM25Plus

from core.documents import Chunk

TOKEN = re.compile(r'[a-zа-яё0-9]+', re.IGNORECASE)
STOP = {'и', 'в', 'во', 'на', 'с', 'со', 'к', 'ко', 'о', 'об', 'по', 'из', 'для',
        'а', 'но', 'это', 'как', 'что', 'ли', 'или', 'же', 'бы', 'я', 'вы', 'мы',
        'он', 'она', 'оно', 'мне', 'вам', 'его', 'ее', 'их', 'этот', 'эта', 'эти'}


def tokens(text: str) -> list[str]:
    """Нормализация одинакова для запроса и базы; отрицания не удаляем."""
    result = []
    for word in TOKEN.findall(text.lower().replace('ё', 'е')):
        if word in STOP:
            continue
        # Лёгкое приведение русских окончаний без загрузки внешней морфологической модели.
        if len(word) > 5 and re.fullmatch('[а-я]+', word):
            for ending in ('иями', 'ами', 'ого', 'ему', 'ыми', 'ими', 'иях', 'ия', 'ий', 'ый', 'ая', 'ое', 'ые', 'ов', 'ам', 'ах', 'ом', 'ей', 'ы', 'и', 'а', 'я', 'у', 'е'):
                if word.endswith(ending) and len(word) - len(ending) >= 4:
                    word = word[:-len(ending)]
                    break
        result.append(word)
    return result


@dataclass(frozen=True)
class Hit:
    """Скор — показатель ранжирования, не вероятность достоверности."""
    chunk: Chunk
    score: float


class SearchIndex:
    """BM25Plus не даёт отрицательных IDF на небольшой демонстрационной базе."""
    def __init__(self, chunks: tuple[Chunk, ...]):
        self.chunks = chunks
        self.tokenized = tuple(tokens(chunk.text + ' ' + (chunk.section or '')) for chunk in chunks)
        self.token_sets = tuple(frozenset(row) for row in self.tokenized)
        # Технический пустой токен позволяет индексировать абзац только со знаками.
        self.bm25 = BM25Plus([row or ['__empty__'] for row in self.tokenized])

    def search(self, query: str, top_k=5) -> tuple[Hit, ...]:
        words = tokens(query)
        if not words:
            return ()
        unique = set(words)
        scores = self.bm25.get_scores(words)
        candidates = [Hit(chunk, float(scores[i])) for i, chunk in enumerate(self.chunks)
                      if self.token_sets[i] & unique]
        candidates.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return tuple(candidates[:top_k])

    def context(self, hits: tuple[Hit, ...], budget: int) -> tuple[Chunk, ...]:
        """К найденному пункту добавляем соседей из того же раздела, пока хватает бюджета."""
        chosen = []
        seen = set()
        used = 0
        positions = {chunk.chunk_id: i for i, chunk in enumerate(self.chunks)}
        # Сначала сами находки: сосед не должен вытеснить более важный результат поиска.
        ordered = [hit.chunk for hit in hits]
        for hit in hits:
            index = positions[hit.chunk.chunk_id]
            for neighbor in self.chunks[max(0, index - 1):index + 2]:
                if neighbor.document_id == hit.chunk.document_id and neighbor.section == hit.chunk.section:
                    ordered.append(neighbor)
        for chunk in ordered:
            if chunk.chunk_id in seen or used + len(chunk.text) > budget:
                continue
            seen.add(chunk.chunk_id)
            chosen.append(chunk)
            used += len(chunk.text)
        return tuple(chosen)
