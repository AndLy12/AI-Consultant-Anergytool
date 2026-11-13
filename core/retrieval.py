"""Детерминированный BM25 baseline; индекс общий, пользовательское состояние отсутствует."""

from dataclasses import dataclass
from collections import defaultdict
import re
from rank_bm25 import BM25Plus

from core.documents import Chunk

TOKEN = re.compile(r'[a-zа-яё0-9]+', re.IGNORECASE)
STOP = {'и', 'в', 'во', 'на', 'с', 'со', 'к', 'ко', 'о', 'об', 'по', 'из', 'для',
        'а', 'но', 'это', 'как', 'что', 'ли', 'или', 'же', 'бы', 'я', 'вы', 'мы',
        'он', 'она', 'оно', 'мне', 'вам', 'его', 'ее', 'их', 'этот', 'эта', 'эти'}
STOP |= {'у', 'нас', 'нам', 'наш', 'наша', 'наши', 'ваш', 'ваша', 'ваши',
         'вашей', 'вашего', 'вашу', 'можно', 'может', 'можете', 'могу', 'нужно',
         'надо', 'хотим', 'хочу', 'есть', 'будет', 'вообще', 'пока', 'так', 'то',
         'того', 'этому', 'этом', 'этим', 'этого', 'эту', 'них', 'все'}
# Сначала длинные окончания: «солнечную» и «солнечные» должны дать общий ключ,
# а «крыша» и «крыше» — совпасть даже при короткой основе. Это эвристика,
# не полноценный морфологический анализ; отрицания сохраняются отдельно.
ENDINGS = ('иями', 'ыми', 'ими', 'ами', 'ого', 'его', 'ому', 'ему', 'иях',
           'ями', 'ую', 'юю', 'ая', 'яя', 'ое', 'ее', 'ие', 'ые', 'ый', 'ий',
           'ой', 'ых', 'их', 'ия', 'ию', 'ии', 'ов', 'ев', 'ам', 'ям', 'ах',
           'ях', 'ом', 'ем', 'ым', 'им', 'ей', 'ью', 'ы', 'и', 'а', 'я', 'у',
           'ю', 'е', 'о', 'ь')


def tokens(text: str) -> list[str]:
    """Словоформы и обозначения единиц нормализуем одинаково в запросе и базе.

    кВт и кВт·ч остаются разными единицами; числовые значения не переводим.
    Нормализация меняет только поисковые ключи, исходные цитаты сохраняются.
    """
    text = text.lower().replace('ё', 'е')
    text = re.sub(r'киловатт[а-я]*[\s-]+час[а-я]*', ' квтч ', text)
    text = re.sub(r'\bквт\s*[·⋅*-]?\s*ч\b', ' квтч ', text)
    text = re.sub(r'\bкиловатт[а-я]*\b', ' квт ', text)
    text = re.sub(r'\bмегаватт[а-я]*\b', ' мвт ', text)
    result = []
    for word in TOKEN.findall(text):
        if word in STOP:
            continue
        # Лёгкое приведение русских окончаний без загрузки внешней морфологической модели.
        if len(word) > 3 and re.fullmatch('[а-я]+', word):
            for ending in ENDINGS:
                if word.endswith(ending) and len(word) - len(ending) >= 3:
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
    """BM25Plus ранжирует разделы и выбирает исходный фрагмент внутри каждого.

    Индексы разделов и абзацев строятся один раз и только читаются при поиске.
    Число и вес совпадений не являются вероятностью правильности ответа.
    """
    def __init__(self, chunks: tuple[Chunk, ...]):
        # Проверяем и готовые фрагменты вызывающей стороны: заголовок не должен
        # превращать пустой разделитель в результат поиска.
        self.chunks = tuple(c for c in chunks if any(char.isalnum() for char in c.text))
        self.groups = defaultdict(list)
        self.guards = defaultdict(list)
        sections = defaultdict(list)
        for chunk in self.chunks:
            path = chunk.section_path or (chunk.section,)
            group = (chunk.document_id, path[:2])
            self.groups[group].append(chunk)
            sections[(chunk.document_id, path)].append(chunk)
        for (document_id, path), group in self.groups.items():
            if any(re.search(r'кому подходит|не подходит|ограничени|границ|не делаем|утверждать нельзя|как работает|результат',
                             heading or '', re.IGNORECASE) for heading in path):
                self.guards[document_id].append(tuple(group))
        # После построения индекс только читается параллельными respond.
        self.groups = {key: tuple(group) for key, group in self.groups.items()}
        self.guards = {key: tuple(groups) for key, groups in self.guards.items()}
        self.tokenized = tuple(tokens(chunk.text + ' ' + (chunk.section or '')) for chunk in self.chunks)
        self.token_sets = tuple(frozenset(row) for row in self.tokenized)
        self.bm25 = BM25Plus([row or ['__empty__'] for row in self.tokenized]) if self.chunks else None
        # Изолированная строка таблицы может выиграть по одному числу, а нужный
        # длинный абзац — проиграть из-за длины. Ранжируем смысловые разделы,
        # затем выбираем внутри каждого исходный фрагмент с прежними ID.
        self.sections = tuple(tuple(group) for group in sections.values())
        section_tokens = tuple(tokens(' '.join((group[0].title, *(group[0].section_path or (group[0].section or '',)),
                                                *(c.text for c in group)))) for group in self.sections)
        self.section_sets = tuple(frozenset(row) for row in section_tokens)
        self.section_bm25 = BM25Plus([row or ['__empty__'] for row in section_tokens]) if self.sections else None
        self.positions = {chunk.chunk_id: i for i, chunk in enumerate(self.chunks)}

    def search(self, query: str, top_k=5) -> tuple[Hit, ...]:
        words = tokens(query)
        if not words or self.bm25 is None:
            return ()
        unique = set(words)
        scores = self.section_bm25.get_scores(words)
        passage_scores = self.bm25.get_scores(words)
        candidates = []
        for i, group in enumerate(self.sections):
            if not self.section_sets[i] & unique:
                continue
            best = min(group, key=lambda c: (-passage_scores[self.positions[c.chunk_id]], c.chunk_id))
            candidates.append(Hit(best, float(scores[i])))
        candidates.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return tuple(candidates[:top_k])

    def context(self, hits: tuple[Hit, ...], budget: int) -> tuple[Chunk, ...]:
        """Дополняем находки целыми разделами и ограничениями тех же документов.

        Одного соседнего абзаца недостаточно для таблицы или оговорки в другом
        подразделе. Дополнения берём целиком, без обрезки условия посередине.
        Бюджет остаётся жёстким: полноту всех условий при малом бюджете не обещаем.
        """
        chosen = []
        seen = set()
        used = 0
        def add(group):
            nonlocal used
            fresh = [c for c in group if c.chunk_id not in seen]
            size = sum(len(c.text) for c in fresh)
            if used + size <= budget:
                chosen.extend(fresh)
                seen.update(c.chunk_id for c in fresh)
                used += size

        # Все исходные находки имеют приоритет над любым расширением контекста.
        for hit in hits:
            add((hit.chunk,))
        groups = []
        documents = []
        for hit in hits:
            path = hit.chunk.section_path or (hit.chunk.section,)
            key = (hit.chunk.document_id, path[:2])
            if key not in groups:
                groups.append(key)
            if hit.chunk.document_id not in documents:
                documents.append(hit.chunk.document_id)
        # Сначала восстанавливаем раздел лучшей находки, затем общие ограничения;
        # оставшиеся разделы заполняют свободное место после защитных оговорок.
        if groups:
            add(self.groups[groups[0]])
        guard_lists = [self.guards.get(document, ()) for document in documents]
        for i in range(max((len(rows) for rows in guard_lists), default=0)):
            for rows in guard_lists:
                if i < len(rows):
                    add(rows[i])
        for key in groups[1:]:
            add(self.groups[key])
        return tuple(chosen)
