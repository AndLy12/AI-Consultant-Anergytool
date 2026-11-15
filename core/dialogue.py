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
    (r'автоматическ\w* уч[её]т|автоматизи\w* сбор|аскуэ|по цехам|по арендатор|облачн\w* платформ|облачн\w*\s+уч[её]т\w*|платформ\w* уч[её]т|\bsim\b|сим[- ]карт|симки|\b(?:уч[её]т|для\s+уч[её]та)\b(?!\s+(?:вод|тепл|газ|реактив))', 'облачная платформа'),
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
# «И» разделяет решения лишь при новом сказуемом. В перечислении
# «панели и оптовый рынок не нужны» отрицание относится к обоим продуктам.
CLAUSES = re.compile(
    r'[.!?;,\n]+|\s+\b(?:но|зато|а(?!\s+не\b))\b\s+|'
    r'\s+и\s+(?=(?:не\s+)?(?:нуж(?:ен|на|но|ны)|отказ\w*|'
    r'рассматрива\w*|планиру\w*)\b)', re.I)
RETURN_FIRST = re.compile(r'верн\w*[^.!?\n]{0,50}к\s+перв\w*\s+(?:вопрос|тем|вариант|продукт|решени)', re.I)
# Отказ может стоять после названия продукта: «от панелей отказались».
# Проверка двойного отрицания ниже относится и к этому порядку слов.
# «Не требуются» описывает отказ, а «не требуют обслуживания» — свойство
# продукта. Нельзя отбрасывать продукт по общему префиксу «требу».
REFUSAL = re.compile(r'\bне\s+(?:нуж(?:ен|на|но|ны)|надо|интерес\w*|рассматрива\w*|планиру\w*|хот\w*|требу(?:ет|ют)ся)\b|\bотказ\w*\s+от\b|\bот\s+[^.!?;\n]{0,100}\bотказ\w*', re.I)
EXCLUDED = re.compile(r'\b(?:исключ[её]н(?:а|о|ы)?|исключаем)\b', re.I)
DOUBLE_NEGATION = re.compile(r'\bне\s+(?:(?:мож\w*|буд\w*|хот\w*|планиру\w*)\s+)?отказ\w*\b|\bне\s+переста\w*\s+рассматрива\w*|\bне\s+(?:исключ[её]н(?:а|о|ы)?|исключаем)\b', re.I)
PROPERTY_ACTION = re.compile(r'менять|замен\w*|покупать|устанавливать|передавать|оплачивать|подключать|обслужива\w*|очища\w*|мыть|ремонтир\w*|чистить', re.I)


@dataclass(frozen=True)
class SearchIntent:
    """Только поисковые признаки; они не заменяют исходные слова и факты клиента."""
    active: tuple[str, ...]
    excluded: tuple[str, ...]
    query: str


def _clauses_with_questions(text: str):
    """Сохраняем вопросительный знак своей части, а не всего сообщения.

    Отказ в первом предложении остаётся отказом, даже если следующее содержит
    вопрос. Сам вопрос «проект исключён?» не является выбором клиента.
    """
    start = 0
    for separator in CLAUSES.finditer(text):
        yield text[start:separator.start()], '?' in separator.group()
        start = separator.end()
    yield text[start:], False


def _topic_events(text: str):
    """Разбираем отказ в пределах части фразы, сохраняя отрицательные свойства.

    «Не нужны панели» исключает продукт; «SIM менять не нужно» — действие.
    Общий отказ от перечисления относится к каждому названному продукту.
    Неуверенные конструкции остаются поисковыми данными, не доказанными фактами.
    """
    for clause, questioned in _clauses_with_questions(text):
        found = [(title, re.search(pattern, clause, re.I)) for pattern, title in TOPICS]
        if (re.search(r'един\w*\s+кабинет', clause, re.I)
                and re.search(r'прибор|сч[её]тчик|показан|электр|арендатор|энерго', text, re.I)
                and not any(title == 'облачная платформа' and match for title, match in found)):
            found.append(('облачная платформа', re.search(r'един\w*\s+кабинет', clause, re.I)))
        # Отрицательный вопрос о необходимости ещё не является отказом:
        # иначе для ответа «станция нам не нужна?» исчезнут солнечные источники.
        # Отказ от покупки/установки продукта отличается от отрицательного
        # свойства «SIM менять не нужно». Последнее не исключает платформу.
        acquisition_refusal = bool(re.search(
            r'\bне\s+(?:хот\w*|планиру\w*|буд\w*)\s+(?:устанавливать|покупать|подключать)\s+'
            r'(?:(?:нов\w*|ваш\w*|эти)\s+)?(?:солнечн\w*\s+(?:панел\w*|станци\w*)|'
            r'панел\w*|облачн\w*\s+платформ\w*|платформ\w*\s+уч[её]та)\b', clause, re.I))
        refusal = bool(not questioned and (REFUSAL.search(clause) or EXCLUDED.search(clause) or acquisition_refusal)
                       and not DOUBLE_NEGATION.search(clause)
                       and (acquisition_refusal or not PROPERTY_ACTION.search(clause)))
        for title, match in sorted((item for item in found if item[1]), key=lambda item: item[1].start()):
            prefix = clause[:match.start()]
            # «Не только X» не равно «не X». Простое «не X, а Y» обрабатывается
            # до BM25; удаление всех предложений со словом «не» потеряло бы свойства.
            direct = bool(not questioned and re.search(r'\b(?:не|без)\s+$', prefix, re.I))
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
        # Текущая реплика выше имеет вес 3. Синонимы получают тот же вес:
        # иначе общие «передавать ресурсы» вытесняют конкретный термин РСО.
        query += '\nотчёт отчёты РСО ресурсоснабжающая организация' * 3
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


# Удалять прежнее значение у нескольких объектов можно только при явном
# совпадении номера, типа объекта и показателя. Полное совпадение фразы
# защищает остальные сведения из той же реплики.
_NUMBERED_POWER = re.compile(
    r'(?:поправка|исправляю|уточняю)?[:\s]*(?:у\s+)?'
    r'(?P<number>перв|втор)(?:ый|ой|ого)\s+(?P<object>склад|завод|офис|магазин)а?'
    r'[:\s]+(?:мощность[:\s]+)?\d+(?:[.,]\d+)?\s*(?:кВт|МВт)', re.I)

_NAMED_POWER = re.compile(
    r'(?:по\s+)?объект(?:у|а)?\s+(?P<label>[а-яa-z]|[0-9]+)\s+'
    r'(?:имеет\s+|[—:-]\s*)?(?:(?:поправка|исправляю|уточняю):\s*)?'
    r'\d+(?:[.,]\d+)?\s*(?:кВт|МВт)', re.I)

# Только самостоятельное утверждение: не удаляем предложение с прочими
# фактами, если невозможно отделить старую мощность без потери его смысла.
_OBJECT_POWER = re.compile(
    r'(?:(?:поправка|исправляю|уточняю)[:\s]+)?(?:у\s+нас\s+)?'
    r'(?:мощность\s+)?(?P<object>склад|офис|завод|магазин)(?:а)?'
    r'\s+(?:мощност(?:ь|ью)\s+)?\d+(?:[.,]\d+)?\s*(?:кВт|МВт)', re.I)


def _current_card_messages(texts, active):
    """Убираем только однозначно заменённые выдержки до разбора остальных поправок."""
    removed = [set() for _ in texts]
    latest = {}
    for turn, text in enumerate(texts):
        for span in re.finditer(r'\S.*?(?:[.!?;](?=\s|$)|,(?=\s*объект\b)|(?=\n|$))', text):
            clean = span.group().rstrip('.;,')
            match = _NUMBERED_POWER.fullmatch(clean)
            if match:
                key = (match['number'].lower(), match['object'].lower())
                if key in latest and re.match(r'поправка|исправляю|уточняю', clean, re.I):
                    old_turn, old_span = latest[key]
                    removed[old_turn].add(old_span)
                latest[key] = (turn, span.span())
            named = _NAMED_POWER.fullmatch(clean)
            if named:
                key = ('named', named['label'].lower())
                if key in latest and re.search(r'поправка|исправляю|уточняю', clean, re.I):
                    old_turn, old_span = latest[key]
                    removed[old_turn].add(old_span)
                latest[key] = (turn, span.span())
            scoped = _OBJECT_POWER.fullmatch(clean)
            if scoped:
                key = ('object', scoped['object'].lower())
                if key in latest and re.match(r'поправка|исправляю|уточняю', clean, re.I):
                    old_turn, old_span = latest[key]
                    removed[old_turn].add(old_span)
                latest[key] = (turn, span.span())
            events = list(_topic_events(clean))
            if (events and all(not enabled and title in active for title, enabled, _ in events)
                    and re.fullmatch(r'[а-яё -]+\s+исключаем', clean, re.I)):
                removed[turn].add(span.span())

    # Возвращаем непрерывные фрагменты исходной реплики: склейка через
    # удалённую фразу не пройдёт проверку происхождения текста карточки.
    result = []
    for text, spans in zip(texts, removed):
        start = 0
        for left, right in sorted(spans):
            if prefix := text[start:left].strip():
                result.append(prefix)
            start = right
        if tail := text[start:].strip():
            result.append(tail)
    return result


def summary_excerpts(texts: list[str], excluded: tuple[str, ...] = (), active: tuple[str, ...] = ()) -> list[str]:
    """Для явных поправок не копируем прежнее значение в запасной черновик.

    Обрабатываем только названные с единицами параметры и явную коррекцию.
    Каждый результат — исходная выдержка; разные объекты и неопределённые ссылки
    не сводим к одному значению. Это не замена смысловой проверке резюме моделью.
    """
    original_texts = texts
    texts = _current_card_messages(texts, active)
    correction = re.compile(
        r'исправля\w*|поправ\w*|уточняю|уточнение|ошибся|ошиблась|'
        r'\bне\s+(?!только\b)[^.!?\n]{1,40}\s+а\s+', re.I)
    if not excluded and not any(correction.search(text) for text in texts):
        return texts
    # При нескольких объектах не сводим мощность к общему слоту. Явные
    # метки уже разрешены выше; неопределённые поправки требуют подтверждения.
    separate_objects = any(_NAMED_POWER.search(text) or (
        correction.search(text)
        and re.search(r'перв\w*|втор\w*|друг\w*|одн\w*\s+из', text, re.I))
        for text in original_texts)
    # «Склад» и «офис» также обозначают разные объекты без номеров. Для
    # них общий слот мощности небезопасен; точные замены обработаны выше,
    # неоднозначные остаются в исходной хронологии для подтверждения.
    separate_objects = separate_objects or any(
        not correction.search(text) and len({match.group(1).lower()
            for match in re.finditer(r'\b(склад|офис|завод|магазин)\w*', text, re.I)}) > 1
        for text in original_texts)
    # Не начинаем число с середины другого числа. Без этой границы длинная
    # последовательность цифр без единицы проверялась со всех позиций
    # и давала квадратичное время разбора допустимого входного сообщения.
    value = r'(?<![\d.,])(?<!\d[ \u00a0])\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?\s*(?:(?:тыс(?:яч\w*)?\.?|млн|миллион\w*)\s*)?'
    quantity = (r'(?:(?:не\s+(?:менее|более|ниже|выше)|от|до|около|примерно)\s+)?' + value
                + r'(?:\s*(?:[-–—]|до)\s*' + value + r')?')
    patterns = (
        ('consumption', re.compile(r'(?:потреблен\w*\s*[:—-]?\s*)?' + quantity +
            r'(?:[км]вт\s*[·⋅*-]?\s*ч|киловатт[- ]?час\w*|мегаватт[- ]?час\w*)'
            r'(?:\s*(?:в|за|/)\s*(?:месяц\w*|год\w*|сут\w*))?', re.I)),
        ('power', re.compile(r'(?:мощност\w*(?:\s+склада)?\s*[:—-]?\s*)?' + quantity +
            r'(?:[км]вт(?!\s*[·⋅*-]?\s*ч\b)|киловатт\w*(?![- ]час)|мегаватт\w*(?![- ]час))\b', re.I)),
        ('object', re.compile(r'\b(?:склад\w*|завод\w*|производств\w*|офис\w*|магазин\w*|бизнес[- ]центр\w*)\b', re.I)),
        ('metering', re.compile(r'\b(?:ручн\w*|автоматическ\w*|автоматизированн\w*)\s+'
                               r'(?:сбор\w*(?:\s+показан\w*)?|уч[её]т\w*)|'
                               r'\bсбор\w*\s+(?:уже\s+)?(?:автоматическ\w*|ручн\w*)|'
                               r'\bвручную\s+снима\w*\s+показан\w*|\bАСКУЭ\b', re.I)),
    )
    records = []
    for text in texts:
        facts = []
        for slot, pattern in (() if separate_objects else patterns):
            facts.extend((slot, match.group(), match.start(), match.end()) for match in pattern.finditer(text))
        # КВт·ч нельзя одновременно принять за мощность, в том числе при
        # написании единицы словами. Более длинный показатель имеет приоритет.
        facts = [fact for fact in facts if fact[0] != 'power' or not any(
            other[0] == 'consumption' and other[2] <= fact[2] < other[3] for other in facts)]
        facts = [fact for fact in facts if not re.search(r'\b(?:не|вместо)\s+$', text[:fact[2]], re.I)]
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
            if correction.search(text[:end]):
                # Исправление в конце реплики заменяет и прежнее значение
                # из её начала, а не только факты предыдущих сообщений.
                replaced = {slot for slot, _ in present}
                records = [(slot, quote) for slot, quote in records if slot not in replaced]
            if excluded:
                # В карточке сохраняем факты об объекте отдельно от снятой темы.
                # Вопросы клиента и фразы об исключённом продукте не являются заявкой.
                if any(title in excluded for title, _, _ in _topic_events(clause)):
                    records.extend((slot, quote) for slot, quote in present
                                   if slot in {'object', 'power', 'consumption'})
                    continue
                if text[end:end + 1] == '?':
                    continue
            # Если показатель уже содержит название объекта, оставляем одну
            # читаемую выдержку («мощность склада 900 кВт»).
            records.extend([(slot, quote) for slot, quote in present
                            if not any(quote != other and quote in other for _, other in present)]
                           or [(None, clause)])
    return list(dict.fromkeys(quote for _, quote in records))


def search_query(request: CoreRequest) -> str:
    """Поисковые синонимы добавляются после разрешения пользовательского намерения."""
    intent = search_intent(request)
    query = intent.query
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
    if not intent.active or 'выход на оптовый рынок' in intent.active:
        # Прилагательное в адресе и название в источнике имеют разные основы.
        # Добавляем только название: ограничение региона определяет документ,
        # а не зашитый в код вердикт о возможности выхода на рынок.
        for pattern, name in ((r'камчатск|камчатк', 'Камчатка'),
                              (r'магаданск|магадан', 'Магадан'),
                              (r'сахалинск|сахалин', 'Сахалин'),
                              (r'чукотск|чукотк', 'Чукотка')):
            if re.search(pattern, query, re.I):
                terms += (' ' + name + ' регион энергосистема') * 3
    return query + terms


def data_checklist_question(text: str) -> bool:
    """Просьбу перечислить исходные данные отличаем от индивидуального расчёта."""
    return bool(re.search(
        r'что\s+(?:нужно|требуется)\s+для\s+(?:предварительной\s+)?оценки\b|'
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
