"""История запроса: разбор CSV-диалога, поисковое намерение и выдержки для черновика."""

import json
import re
from dataclasses import dataclass
from core_contract import CoreRequest, Turn

ROLE_LINE = re.compile(r'^\s*(user|пользователь|клиент|assistant|ассистент|консультант)\s*:\s*(.*)$', re.IGNORECASE)

# Каталог нужен и поиску, и разбору намерения: два разных словаря расходились бы
# на отрицаниях. Это ограниченная эвристика, а не универсальный разбор русского языка.
TOPICS = (
    # Родительный падеж в явном назначении «для учёта» сохраняет тему.
    # Произвольное «система учёта» не определяет продукт клиента; остальные
    # ресурсы и реактивная энергия по-прежнему не назначаются этой платформе.
    (r'автоматическ\w* уч[её]т|автоматизи\w* сбор|аскуэ|по цехам|по арендатор|облачн\w* платформ|платформ\w* уч[её]т|\bsim\b|сим[- ]карт|симки|\b(?:уч[её]т|для\s+уч[её]та)\b(?!\s+(?:вод|тепл|газ|реактив))', 'облачная платформа'),
    (r'\bфото\b|фотограф|снимк|распозна', 'распознавание показаний'),
    (r'солнечн|фотоэлектр|панел|крыш|кровл|скат|фасад', 'солнечные электростанции'),
    (r'ценов\w* категори', 'ценовые категории'),
    (r'оптов\w* рын|орэм', 'выход на оптовый рынок'),
    (r'коммерческ\w* потер|находить потери|хищени', 'поиск коммерческих потерь'),
)
PREPARATION_SECTIONS = {
    'облачная платформа': 'для платформы учёта',
    'солнечные электростанции': 'для солнечной станции',
    'распознавание показаний': 'для распознавания показаний',
    'поиск коммерческих потерь': 'для поиска коммерческих потерь',
    'выход на оптовый рынок': 'для анализа затрат и выхода на оптовый рынок',
}
CLAUSES = re.compile(r'[.!?;,\n]+|\s+\b(?:но|зато|а(?!\s+не\b))\b\s+', re.I)
RETURN_FIRST = re.compile(r'верн\w*[^.!?\n]{0,50}к\s+перв\w*\s+(?:вопрос|тем|вариант)', re.I)
# Отказ может стоять после названия продукта: «от панелей отказались».
# Проверка двойного отрицания ниже относится и к этому порядку слов.
# «Не требуются» описывает отказ, а «не требуют обслуживания» — свойство
# продукта. Нельзя отбрасывать продукт по общему префиксу «требу».
REFUSAL = re.compile(r'\bне\s+(?:нуж\w*|надо|интерес\w*|рассматрива\w*|планиру\w*|хот\w*|требу(?:ет|ют)ся)\b|\bотказ\w*\s+от\b|\bот\s+[^.!?;\n]{0,100}\bотказ\w*', re.I)
DOUBLE_NEGATION = re.compile(r'\bне\s+(?:(?:мож\w*|буд\w*|хот\w*)\s+)?отказ\w*\b|\bне\s+переста\w*\s+рассматрива\w*', re.I)
PROPERTY_ACTION = re.compile(r'менять|замен\w*|покупать|устанавливать|передавать|оплачивать|подключать', re.I)


@dataclass(frozen=True)
class SearchIntent:
    """Только поисковые признаки; они не заменяют исходные слова и факты клиента."""
    active: tuple[str, ...]
    excluded: tuple[str, ...]
    query: str


def _topic_events(text: str):
    """Разбираем отказ в пределах части фразы, сохраняя отрицательные свойства.

    «Не нужны панели» исключает продукт; «SIM менять не нужно» — действие.
    Общий отказ от перечисления относится к каждому названному продукту.
    Неуверенные конструкции остаются поисковыми данными, не доказанными фактами.
    """
    for clause in CLAUSES.split(text):
        found = [(title, re.search(pattern, clause, re.I)) for pattern, title in TOPICS]
        if (re.search(r'един\w*\s+кабинет', clause, re.I)
                and re.search(r'прибор|сч[её]тчик|показан|электр|арендатор|энерго', text, re.I)
                and not any(title == 'облачная платформа' and match for title, match in found)):
            found.append(('облачная платформа', re.search(r'един\w*\s+кабинет', clause, re.I)))
        refusal = bool(REFUSAL.search(clause) and not DOUBLE_NEGATION.search(clause)
                       and not PROPERTY_ACTION.search(clause))
        for title, match in sorted((item for item in found if item[1]), key=lambda item: item[1].start()):
            prefix = clause[:match.start()]
            # «Не только X» не равно «не X». Простое «не X, а Y» обрабатывается
            # до BM25; удаление всех предложений со словом «не» потеряло бы свойства.
            direct = bool(re.search(r'\b(?:не|без)\s+$', prefix, re.I))
            yield title, not (refusal or direct), clause.strip()


def search_intent(request: CoreRequest) -> SearchIntent:
    """Последняя пользовательская тема имеет приоритет перед предположением ассистента.

    Исключения действуют до явного повторного включения. Для смешанного сбора
    учёт и фото сохраняются вместе, пока клиент не переключил продукт.
    Вопрос о категории связывается с коротким пользовательским ответом без
    копирования утверждений ассистента. История генерации сохраняет роли.
    """
    texts = []
    questions = []
    last_question = ''
    for turn in [*request.history, Turn(role='user', content=request.message)]:
        if turn.role == 'assistant':
            match = re.search(r'([^.!?\n]{1,300})\?\s*$', turn.content)
            last_question = match[1] if match else ''
        else:
            texts.append(turn.content)
            questions.append(last_question)
            last_question = ''
    excluded = set()
    active = []
    first = ()
    for text, question in zip(texts, questions):
        events = list(_topic_events(text))
        positive = list(dict.fromkeys(title for title, enabled, _ in events if enabled))
        if (not events and re.search(r'ценов\w*\s+категори', question, re.I)
                and re.search(r'перв\w*|втор\w*|трет\w*|четв[её]рт\w*|пят\w*|шест\w*|\b[1-6](?:-?[яй])?\b', text, re.I)):
            # Короткий ответ подтверждает тему вопроса, но не утверждения
            # ассистента о мощности или порогах. В BM25 попадёт лишь имя темы.
            positive = ['ценовые категории']
        if not first and positive:
            first = tuple(positive)
        if RETURN_FIRST.search(text) and not positive:
            positive = list(first)
            excluded.difference_update(positive)
        for title, enabled, _ in events:
            if enabled:
                excluded.discard(title)
            else:
                excluded.add(title)
        if positive:
            linked = set(active + positive)
            if (linked <= {'облачная платформа', 'распознавание показаний'}
                    and not re.search(r'теперь|вместо|только|переключ|не\s+нуж', text, re.I)):
                active = list(dict.fromkeys(active + positive))
            else:
                active = positive
        active = [title for title in active if title not in excluded]
    allowed = set(active)

    def relevant(text):
        parts = []
        for clause in CLAUSES.split(text):
            events = list(_topic_events(clause))
            mentioned = {title for title, _, _ in events}
            if (not mentioned or mentioned & allowed) and not mentioned & excluded:
                parts.append(clause.strip())
        return '. '.join(part for part in parts if part)

    current = relevant(request.message)
    previous = [relevant(text) for text in texts[:-1]][-3:]
    # Не передаём сырые утверждения ассистента в BM25: его прежняя ошибка
    # иначе становится дополнительным основанием для неверного продукта.
    query = (current + '\n') * 3 + '\n'.join(previous)[-6000:] + '\n' + ' '.join(active)
    if re.search(r'отч[её]т', current, re.I) and re.search(r'поставщик|ресурсоснабж|\bрсо\b', current, re.I):
        # Связываем разговорное название адресата отчёта с термином корпуса.
        # Это только поисковые синонимы: они не задают продукт и не содержат
        # ответа о том, кто передаёт отчёт; явные исключения остаются в силе.
        query += '\nотчёт отчёты РСО ресурсоснабжающая организация'
    return SearchIntent(tuple(active), tuple(sorted(excluded)), query)


def parse_question(text: str) -> CoreRequest:
    """Понимаем явный JSON-диалог и строки с ролями только user/assistant.

    Это парсер независимого тест-кейса платформы, не доверенная история публичного API.
    Неизвестный формат остаётся одним пользовательским сообщением; system не повышает роль.
    """
    if not text.strip():
        return CoreRequest(message='Пользователь отправил пустой вопрос.')
    if text.lstrip().startswith(('[', '{')):
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            data = None
        if isinstance(data, dict) and set(data) == {'messages'}:
            data = data['messages']
        if isinstance(data, list) and data and all(
                isinstance(row, dict) and set(row) == {'role', 'content'}
                and row['role'] in {'user', 'assistant'} and isinstance(row['content'], str)
                and row['content'].strip() for row in data):
            turns = [Turn.model_validate(row) for row in data]
            if turns[-1].role != 'user':
                raise ValueError('Диалог должен завершаться репликой пользователя')
            return CoreRequest(message=turns[-1].content, history=turns[:-1])
    turns = []
    for line in text.splitlines():
        match = ROLE_LINE.match(line)
        if match:
            role = 'user' if match[1].lower() in {'user', 'пользователь', 'клиент'} else 'assistant'
            turns.append({'role': role, 'content': match[2]})
        elif turns:
            turns[-1]['content'] += '\n' + line
        elif line.strip():
            # Вступительный текст до ролей нельзя терять или выдавать за служебную инструкцию.
            return CoreRequest(message=text)
    if turns and turns[-1]['role'] == 'user' and all(t['content'].strip() for t in turns):
        return CoreRequest(message=turns[-1]['content'], history=turns[:-1])
    return CoreRequest(message=text)


def bounded_history(request: CoreRequest, budget: int) -> tuple[list[dict], bool]:
    """Передаём последние целые реплики; текущий вопрос не обрезаем и не дублируем."""
    chosen = []
    size = 0
    for turn in reversed(request.history):
        if size + len(turn.content) > budget:
            break
        chosen.append({'role': turn.role, 'content': turn.content})
        size += len(turn.content)
    chosen.reverse()
    return chosen, len(chosen) != len(request.history)


def summary_excerpts(texts: list[str]) -> list[str]:
    """Для явных поправок не копируем прежнее значение в запасной черновик.

    Обрабатываем только названные с единицами параметры и явную коррекцию.
    Каждый результат — исходная выдержка; разные объекты и неопределённые ссылки
    не сводим к одному значению. Это не замена смысловой проверке резюме моделью.
    """
    correction = re.compile(
        r'исправля\w*|поправ\w*|уточняю|ошибся|ошиблась|'
        r'\bне\s+(?!только\b)[^.!?\n]{1,40}\s+а\s+', re.I)
    if not any(correction.search(text) for text in texts):
        return texts
    value = r'\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?\s*(?:(?:тыс(?:яч\w*)?\.?|млн|миллион\w*)\s*)?'
    quantity = (r'(?:(?:не\s+(?:менее|более|ниже|выше)|от|до|около|примерно)\s+)?' + value
                + r'(?:\s*(?:[-–—]|до)\s*' + value + r')?')
    patterns = (
        ('consumption', re.compile(r'(?:потреблен\w*\s*[:—-]?\s*)?' + quantity +
            r'(?:[км]вт\s*[·⋅*-]?\s*ч|киловатт[- ]?час\w*|мегаватт[- ]?час\w*)'
            r'(?:\s*(?:в|за|/)\s*(?:месяц\w*|год\w*|сут\w*))?', re.I)),
        ('power', re.compile(r'(?:мощност\w*\s*[:—-]?\s*)?' + quantity +
            r'(?:[км]вт(?!\s*[·⋅*-]?\s*ч\b)|киловатт\w*(?![- ]час)|мегаватт\w*(?![- ]час))\b', re.I)),
        ('object', re.compile(r'\b(?:склад\w*|завод\w*|производств\w*|офис\w*|магазин\w*|бизнес[- ]центр\w*)\b', re.I)),
        ('metering', re.compile(r'\b(?:ручн\w*|автоматическ\w*|автоматизированн\w*)\s+'
                               r'(?:сбор\w*(?:\s+показан\w*)?|уч[её]т\w*)|\bАСКУЭ\b', re.I)),
    )
    records = []
    for text in texts:
        # У разных объектов одноимённые параметры не являются противоречием.
        # Для таких поправок оставляем исходную хронологию для ручного разбора.
        if correction.search(text) and re.search(r'перв\w*|втор\w*|друг\w*|одн\w*\s+из', text, re.I):
            return texts
        facts = []
        for slot, pattern in patterns:
            facts.extend((slot, match.group(), match.start(), match.end()) for match in pattern.finditer(text))
        # КВт·ч нельзя одновременно принять за мощность, в том числе при
        # написании единицы словами. Более длинный показатель имеет приоритет.
        facts = [fact for fact in facts if fact[0] != 'power' or not any(
            other[0] == 'consumption' and other[2] <= fact[2] < other[3] for other in facts)]
        facts = [fact for fact in facts if not re.search(r'\b(?:не|вместо)\s+$', text[:fact[2]], re.I)]
        if correction.search(text):
            replaced = {slot for slot, _, _, _ in facts}
            records = [(slot, quote) for slot, quote in records if slot not in replaced]
        # Не разрываем десятичные числа и «тыс.» внутри уже найденной величины.
        boundaries = [0]
        for match in re.finditer(r'[.!?;,\n]+', text):
            if any(start <= match.start() < end for _, _, start, end in facts):
                continue
            boundaries.extend((match.start(), match.end()))
        boundaries.append(len(text))
        for start, end in zip(boundaries[::2], boundaries[1::2]):
            clause = text[start:end]
            clause = clause.strip()
            if not clause:
                continue
            if correction.search(text) and re.match(
                    r'^(?:а\s+)?не\s+(?!менее\b|более\b|ниже\b|выше\b|меньше\b|больше\b)', clause, re.I):
                continue
            present = [(slot, quote) for slot, quote, _, _ in facts if quote in clause]
            records.extend(present or [(None, clause)])
    return list(dict.fromkeys(quote for _, quote in records))


def search_query(request: CoreRequest) -> str:
    """Поисковые синонимы добавляются после разрешения пользовательского намерения."""
    query = search_intent(request).query
    # Разговорное «проверите учёт» в материалах может называться аудитом.
    terms = ''
    if re.search(r'провер\w*.{0,28}уч[её]т|уч[её]т.{0,28}провер', query, re.I):
        terms = ' аудит существующей системы учёта модернизация приборов'
    # Поисковые синонимы связывают язык клиента с формулировкой документа.
    # Тексты ответа и источников остаются исходными.
    if re.search(r'фасад|на стен[еуы]', query, re.I):
        terms += ' монтаж на вертикальные поверхности зданий'
    if re.search(r'архив|суточн', query, re.I):
        terms += ' глубина архива разбивка день ночь идентификатор канала'
    return query + terms


def data_checklist_question(text: str) -> bool:
    """Просьбу перечислить исходные данные отличаем от индивидуального расчёта."""
    return bool(re.search(
        r'понадоб|потребу(?:ет|ют)ся|с чего(?:\s+\w+){0,2}\s+нач|'
        r'каки\w*(?:\s+\w+){0,3}\s+(?:данн|сведен|параметр)|'
        r'что(?:\s+\w+){0,4}\s+(?:предостав|подготов|узна|уточн|сообщ|собра|расч[её]т)', text, re.I))


def data_sufficiency_question(text: str) -> bool:
    """Проверке достаточности нужен полный список условий, как и подготовке данных."""
    return bool(re.search(
        r'(?:данн|архив|показан)[^.!?\n]{0,60}(?:хватит|достаточ)|'
        r'(?:хватит|достаточ)[^.!?\n]{0,60}(?:данн|архив|показан)', text, re.I))


def load_profile_question(text: str) -> bool:
    """Выделяем вопрос о времени нагрузки без потери второго явного запроса.

    Если клиент также спрашивает цену, диапазон мощности или размещение,
    сохраняем полный продуктовый контекст с соответствующими ограничениями.
    """
    return bool(re.search(r'вечер|ноч', text, re.I)) and not re.search(
        r'типов|диапазон|мощност|стоим|цен|тариф|кровл|скат|фасад|площад|'
        r'\b\d+\s*(?:квт|мвт|киловатт)', text, re.I)
