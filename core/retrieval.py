"""Детерминированный BM25 baseline; индекс общий, пользовательское состояние отсутствует."""

from dataclasses import dataclass
from collections import defaultdict
import re
from rank_bm25 import BM25Plus

from core.documents import Chunk
from core.dialogue import search_query, load_profile_question

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
    text = re.sub(r'\bsim\b', 'сим', text)
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


CATEGORY_WORDS = tuple(tokens(word)[0] for word in
                       ('первая', 'вторая', 'третья', 'четвертая', 'пятая', 'шестая'))


def price_category(text):
    """Связываем словесную категорию и форму «4-я» с заголовком исходного абзаца."""
    words = [word for word in tokens(text) if word in CATEGORY_WORDS]
    if words:
        return words[-1]
    numbers = re.findall(r'\b([1-6])\s*-?\s*(?:я|й|ая|ой|ую)\b', text, re.I)
    number = numbers[-1] if numbers else text.strip()
    return CATEGORY_WORDS[int(number) - 1] if number in {'1', '2', '3', '4', '5', '6'} else None


@dataclass(frozen=True)
class Hit:
    """Скор — показатель ранжирования, не вероятность достоверности."""
    chunk: Chunk
    score: float
    required: bool = False


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
            if any(re.search(r'кому подходит|не подходит|ограничени|границ|не делаем|утверждать нельзя|как работает|результат|факты, которые можно утверждать',
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

    def search_request(self, request, top_k=5):
        """Сохраняем общий поиск, выделяя место продукту, названному в диалоге.

        Явная смена продукта в последней реплике важнее ранней темы. Подсказки
        относятся к словам каталога, а не к ID вопросов или готовым ответам.
        """
        query = search_query(request)
        ranked = self.search(query, len(self.sections))
        hits = ranked[:top_k]
        checklist_found = False
        topics = (
            (r'автоматическ\w* уч[её]т|автоматизи\w* сбор|аскуэ|по цехам|по арендатор|облачн\w* платформ|платформ\w* уч[её]т|sim|сим-карт|симки', 'облачная платформа'),
            (r'\bфото\b|фотограф|снимк|распознаван', 'распознавание показаний'),
            (r'солнечн|фотоэлектр|панел|крыш|кровл|скат|фасад', 'солнечные электростанции'),
            (r'ценов\w* категори', 'ценовые категории'),
            (r'оптов\w* рын|орэм', 'выход на оптовый рынок'),
            (r'коммерческ\w* потер|находить потери|хищени', 'поиск коммерческих потерь'),
        )
        planning = re.search(
            r'понадоб|с чего(?:\s+\w+){0,2}\s+нач|'
            r'каки\w*(?:\s+\w+){0,3}\s+(?:данн|сведен|параметр)|'
            r'что(?:\s+\w+){0,4}\s+(?:предостав|подготов|узна|уточн|сообщ|собра)',
            request.message, re.I)
        turns = [request.message]
        if re.search(r'верн\w*[^.!?\n]{0,50}к\s+перв\w*\s+(?:вопрос|тем)', request.message, re.I):
            # Явная ссылка на первый вопрос важнее последней промежуточной темы.
            # Берём первую названную пользователем тему, пропуская приветствия.
            first_topic = next((h.content for h in request.history if h.role == 'user'
                                and any(re.search(pattern, h.content, re.I) for pattern, _ in topics)), None)
            if first_topic:
                turns.append(first_topic)
        turns.extend(h.content for h in reversed(request.history))
        for turn in turns:
            titles = [title for pattern, title in topics if re.search(pattern, turn, re.I)]
            if planning and titles and set(titles) <= {'облачная платформа', 'распознавание показаний'}:
                # При подготовке смешанного учёта фото — способ получения данных,
                # а не обязательно смена задачи подключения платформы. Сохраняем
                # оба продукта, только если сам пользователь обсуждал оба способа.
                user_text = request.message + '\n' + '\n'.join(
                    h.content for h in request.history if h.role == 'user')
                linked = [title for pattern, title in topics[:2] if re.search(pattern, user_text, re.I)]
                if titles and len(linked) == 2 and set(titles) <= set(linked):
                    titles = linked
            if titles:
                # Сначала ранжируем по самому вопросу, затем выбираем лучшие
                # разделы нужного документа. Остальные места доступны общему поиску.
                product = [h for h in ranked if any(t in h.chunk.title.lower() for t in titles)]
                if product:
                    # При двух способах сбора данных оставляем место обоим продуктам.
                    primary = []
                    for title in titles:
                        match = next((h for h in product if title in h.chunk.title.lower()), None)
                        if match:
                            primary.append(match)
                    primary.extend(h for h in product[:2] if h not in primary)
                    primary = primary[:min(2, top_k)]
                    # Запрос о составе данных относится также к разделу подготовки
                    # расчёта. Заголовок связывает продукт с инструкцией другого документа.
                    if planning:
                        sections = {'облачная платформа': 'для платформы учёта',
                                    'солнечные электростанции': 'для солнечной станции',
                                    'распознавание показаний': 'для распознавания показаний',
                                    'поиск коммерческих потерь': 'для поиска коммерческих потерь',
                                    'выход на оптовый рынок': 'для анализа затрат и выхода на оптовый рынок'}
                        headings = {sections[t] for t in titles if t in sections}
                        guidance = [h for h in ranked if (h.chunk.section or '').lower() in headings]
                        if guidance:
                            # При уже выбранном продукте конкретный список данных
                            # важнее общей инструкции начать знакомство с клиентом.
                            # Для смешанного сбора сохраняем оба списка подготовки.
                            primary = [Hit(h.chunk, h.score, True) for h in guidance] + [
                                h for h in primary if h not in guidance]
                            checklist_found = True
                    if ('солнечные электростанции' in titles
                            and load_profile_question(request.message)):
                        # Дневная выработка и вечерняя нагрузка сформулированы
                        # разными словами. Выносим исходную оговорку о несовпадении
                        # профилей вперёд, не делая вывода за пользователя.
                        mismatch = next((h for h in product if re.search(
                            r'выработк\w*\s+не\s+совпад', h.chunk.text, re.I)), None)
                        if mismatch:
                            primary = [Hit(mismatch.chunk, mismatch.score, True)] + [
                                h for h in primary if h.chunk.chunk_id != mismatch.chunk.chunk_id]
                    if 'ценовые категории' in titles:
                        # У короткого абзаца отдельной категории меньше общих слов,
                        # чем у обзорного раздела. Явно названная категория имеет
                        # приоритет; цифры и условия берём из исходного текста.
                        category = None
                        for text in [request.message] + [h.content for h in reversed(request.history) if h.role == 'user']:
                            category = price_category(text)
                            if category:
                                break
                        for chunk in self.chunks if category else ():
                            heading = re.match(r'^[\s*]*(\w+):', chunk.text)
                            if ('ценовые категории' in chunk.title.lower() and heading
                                    and tokens(heading[1]) == [category]):
                                primary = [Hit(chunk, 0.0, True)] + [
                                    h for h in primary if h.chunk.chunk_id != chunk.chunk_id]
                                break
                    ids = {h.chunk.chunk_id for h in primary}
                    remaining = [h for h in hits if h.chunk.chunk_id not in ids and not (
                        checklist_found and h.chunk.title.lower() in {
                            'общие вопросы', 'обзор продуктов: что подходит под вашу задачу'})]
                    return tuple((primary + remaining)[:top_k])
                break
        return hits

    def rerank_candidates(self, hits, budget=48000, limit=96):
        """Добавляем абзацы двух ведущих документов для семантического поиска.

        Табличная строка или короткая оговорка может не содержать ни одного
        слова запроса. Чередуем документы, чтобы длинный первый не вытеснил второй.
        """
        candidates = list(hits)
        seen = {h.chunk.chunk_id for h in hits}
        used = sum(len(h.chunk.text) for h in hits)
        documents = list(dict.fromkeys(h.chunk.document_id for h in hits))[:2]
        groups = [[c for c in self.chunks if c.document_id == doc] for doc in documents]
        for i in range(max(map(len, groups), default=0)):
            for group in groups:
                if len(candidates) >= limit:
                    return tuple(candidates)
                if i < len(group):
                    chunk = group[i]
                    if chunk.chunk_id not in seen and used + len(chunk.text) <= budget:
                        candidates.append(Hit(chunk, 0.0))
                        seen.add(chunk.chunk_id)
                        used += len(chunk.text)
        return tuple(candidates)

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

        if hits and hits[0].required and re.search(
                r'выработк\w*\s+не\s+совпад', hits[0].chunk.text, re.I):
            # Для вопроса о времени нагрузки оставляем исходную оговорку,
            # дневной профиль и резерв. Общие цены и диапазоны мощности здесь
            # провоцируют подмену показателя и не отвечают на текущий вопрос.
            first = hits[0].chunk
            add((first,))
            for chunk in self.chunks:
                section = (chunk.section or '').lower()
                if chunk.document_id == first.document_id and (
                        section == 'лучший профиль клиента'
                        or re.search(r'если пропад[её]т электричество', section)):
                    add((chunk,))
            return tuple(chosen)

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
