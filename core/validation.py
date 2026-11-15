"""Проверка цитат и ответа модели; наличие оснований не доказывает весь пересказ."""

from typing import Annotated, Literal
from copy import deepcopy
import re
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from core_contract import CoreResponse, CoreUnavailable, ModelResponseError, Source
from core.dialogue import (search_intent, summary_excerpts, data_checklist_question,
                           summary_control_message, APPROXIMATION, build_consultation_state,
                           SUMMARY_REQUEST, SUMMARY_DECLINED)
from core.retrieval import tokens
from core.config import redact_secret


INLINE_ALIAS = re.compile(r'\s*(?:\((D\d+(?:\s*,\s*D\d+)*)\)|\[(D\d+(?:\s*,\s*D\d+)*)\])')
# Просьба уточнить сведения уже является следующим шагом, даже без «?».
# Словарь названий параметров пропускал вежливые вставки и новые параметры,
# из-за чего review добавлял ту же просьбу второй раз отдельным вопросом.
EMBEDDED_QUESTION = re.compile(r'\b(?:уточните|подскажите|сообщите|укажите|назовите)\b', re.I)
# Вопрос в явно названной кнопке/разделе не обращён к клиенту. Убираем
# такие подписи только из проверки следующего шага, сохраняя ответ дословно.
INTERFACE_LABEL = re.compile(
    r'\b(?:кнопк\w*|раздел\w*|метк\w*|пункт\w*|поле|поля|надпис\w*)\s+'
    r'(?:«[^»\n]*»|"[^"\n]*"|“[^”\n]*”)', re.I)
FREE_WORK = re.compile(
    r'\bбесплатн\w*\s+(?P<work>(?:(?:выездн\w*|энергетическ\w*|предварительн\w*)\s+)?'
    r'(?:обследован\w*|аудит\w*|монтаж\w*))', re.I)


def checklist_question_relevant(question: str, message: str, sources: list[Source]) -> bool:
    """Отдельное уточнение после списка должно иметь предмет в запросе или цитатах.

    Это консервативный лексический фильтр, а не проверка смысла вопроса моделью.
    Общие слова «объект», «данные» и предложение провести расчёт не доказывают
    полезность нового параметра. Известность и необходимость проверяет модель.
    Используем нормализацию поиска, сохраняя общие термины и единицы одинаковыми.
    """
    def words(text):
        text = re.sub(r'\bсч[её]тчик\w*', 'прибор', text, flags=re.I)
        return set(tokens(text))

    generic = words('какой какая какие какова каковы сколько объект данные параметры '
                    'уточните сообщите подскажите назовите пожалуйста уже установлен '
                    'сделать провести начать продолжить расчёт оценка консультация шаг')
    subject = words(question) - generic
    context = words(message + '\n' + '\n'.join(source.excerpt for source in sources))
    return bool(subject.intersection(context))


def _visible_summary_covers_draft(answer, draft):
    """Не дублируем оформленное резюме, если оно покрывает актуальные выдержки.

    Лексическое покрытие консервативно: оно не доказывает весь смысл пересказа.
    Вызывается только при явном запросе резюме. Заголовок не обязателен:
    покрытие фактов важнее оформления; числа и оговорки остаются обязательны.
    """
    metadata = set(tokens('исправляю поправка уточняю уточнение по свежей выгрузке всё-таки'))
    facts = set(tokens(draft)) - metadata
    return bool(facts) and facts.issubset(tokens(answer))


def _summary_excerpt_matches(line, texts):
    """Дословность не позволяет отрезать цифру, единицу или оговорку величины.

    Это локальная проверка границ выдержки, не доказательство всех смысловых
    утверждений. При сомнении используется полный актуальный клиентский текст.
    """
    for text in texts:
        for match in re.finditer(re.escape(line), text):
            prefix, suffix = text[:match.start()], text[match.end():]
            if ((prefix and re.match(r'\w', line) and re.search(r'\w$', prefix))
                    or (suffix and re.search(r'\w$', line) and re.match(r'\w', suffix))):
                continue
            if re.match(r'\d', line) and re.search(
                    r'(?:\d[ \u00a0]+|[.,+−-]|(?:' + APPROXIMATION + r'|от|до|не\s+'
                    r'(?:менее|более|выше|ниже))\s+)$', prefix, re.I):
                continue
            if (re.search(r'(?:[км]вт|киловатт\w*|мегаватт\w*)$', line, re.I)
                    and re.match(r'\s*[·⋅*-]?\s*(?:ч\b|час\w*)', suffix, re.I)):
                continue
            return True
    return False


def _negated_free_work(text, match):
    """Сохраняем прямое отрицание обещания бесплатных работ."""
    prefix = text[max(0, match.start() - 80):match.start()]
    suffix = text[match.end():match.end() + 80]
    return bool(re.search(
        r'(?:\bне\s+(?:обещ\w*|гарантир\w*|подтвержд\w*)(?:\s+(?:вам|клиенту))?\s+|'
        r'\bнельзя\s+(?:обещать|гарантировать|подтвердить)\s+|\bне\s+)$', prefix, re.I)
        or re.match(r'\s+(?:в\s+материалах\s+)?не\s+'
                    r'(?:подтвержден\w*|предусмотрен\w*|указан\w*|описан\w*|гарантируется)', suffix, re.I))


def grounded_free_work(answer, evidence_texts):
    """Убираем неподтверждённую бесплатность перед названием физических работ.

    Бесплатный расчёт не подтверждает бесплатное обследование. Проверяется
    только эта лексическая форма; полную смысловую точность она не доказывает.
    Удаление прилагательного не объявляет работу платной и не назначает цену.
    """
    supported = set()
    for text in evidence_texts:
        for match in FREE_WORK.finditer(text):
            if not _negated_free_work(text, match):
                supported.add(re.search(r'обследован|аудит|монтаж', match['work'], re.I)[0].lower())
    def replace(match):
        kind = re.search(r'обследован|аудит|монтаж', match['work'], re.I)[0].lower()
        return match[0] if kind in supported or _negated_free_work(answer, match) else match['work']
    return FREE_WORK.sub(replace, answer)


class Evidence(BaseModel):
    """Модель выбирает фрагмент, но не задаёт его название и координаты."""
    model_config = ConfigDict(extra='forbid', strict=True)
    chunk_id: str = Field(min_length=1, max_length=200)
    quote: str | None = Field(default=None, min_length=1, max_length=3500)


class ModelSelection(BaseModel):
    """Схема не даёт модели добавить команды backend или неоговорённые поля."""
    model_config = ConfigDict(extra='forbid', strict=True)
    status: Literal['answered', 'clarification_needed', 'insufficient_data', 'out_of_scope']
    answer: str | None = Field(default=None, min_length=1, max_length=6000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=3)
    question: str | None = Field(default=None, max_length=300)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    summary_draft: str | None = Field(default=None, max_length=3000)

    # При подмене входного типа для коротких ID Pydantic не переносит Field
    # из основного объявления в JSON Schema. Без ограничения здесь провайдер
    # возвращал четыре источника, а assemble отклонял результат review.
    @field_validator('evidence', mode='before',
                     json_schema_input_type=Annotated[list[Evidence | str], Field(max_length=3)])
    @classmethod
    def parse_source_ids(cls, values):
        """Короткий ID ссылается на тот же фрагмент, что и объект с chunk_id."""
        if isinstance(values, list):
            return [{'chunk_id': v} if isinstance(v, str) else v for v in values]
        return values

    @model_validator(mode='after')
    def consistency(self):
        if self.answer is not None and (not self.answer.strip() or self.status != 'answered'):
            raise ValueError('Связный ответ допустим только для answered')
        if (self.status == 'answered') != bool(self.evidence):
            raise ValueError('Основания не соответствуют статусу')
        if self.status == 'clarification_needed' and (not self.question or not self.question.strip()):
            raise ValueError('Нет уточняющего вопроса')
        if self.question is not None and (
                self.status not in {'answered', 'clarification_needed'} or not self.question.strip()):
            raise ValueError('Лишний или пустой уточняющий вопрос')
        if sum(len(e.quote or "") for e in self.evidence) > 3500:
            raise ValueError('Цитаты слишком длинные')
        if any(not value.strip() or len(value) > 300 for value in self.missing_information):
            raise ValueError('Некорректное описание недостающих сведений')
        # JSON допускает escape одиночного суррогата, который затем нельзя
        # передать в UTF-8 или записать в CSV. Это ошибка ответа модели.
        texts = [self.answer, self.question, self.summary_draft, *self.missing_information]
        texts.extend(value for evidence in self.evidence for value in (evidence.chunk_id, evidence.quote))
        for text in texts:
            if text is not None:
                text.encode('utf-8')
        return self


class AnswerReview(BaseModel):
    """Внутренняя проверка обязана вернуть окончательный объект консультации."""
    model_config = ConfigDict(extra='forbid', strict=True)
    issues: list[Annotated[str, Field(max_length=240)]] = Field(max_length=3)
    follow_up_question: Annotated[str, Field(min_length=3, max_length=300)] | None
    result: ModelSelection


def generation_schema():
    """Передаём провайдеру обязательные поля каждого статуса, включая основания.

    Pydantic-валидаторы после генерации остаются обязательными: поддержка JSON
    Schema у провайдера не гарантируется, а существование цитаты схема не проверяет.
    Схема выводится из общего контракта, чтобы ограничения полей не расходились.
    """
    base = ModelSelection.model_json_schema()
    definitions = base.pop('$defs', {})
    branches = []
    for status in ('answered', 'clarification_needed', 'insufficient_data', 'out_of_scope'):
        branch = deepcopy(base)
        branch['properties']['status'] = {'const': status, 'type': 'string'}
        branch['required'] = ['status', 'evidence']
        if status == 'answered':
            branch['required'].append('answer')
            branch['properties']['answer'] = {'type': 'string', 'minLength': 1, 'maxLength': 6000}
            branch['properties']['evidence']['minItems'] = 1
        else:
            branch['properties']['answer'] = {'type': 'null'}
            branch['properties']['evidence']['maxItems'] = 0
            if status == 'clarification_needed':
                branch['required'].append('question')
                branch['properties']['question'] = {'type': 'string', 'minLength': 1, 'maxLength': 300}
            else:
                branch['properties']['question'] = {'type': 'null'}
        branches.append(branch)
    return {'$defs': definitions, 'oneOf': branches}


def review_schema():
    """Результат review имеет те же обязательные поля статуса, что и генерация.

    Обычная схема Pydantic не выражает межполевой валидатор: answered без
    evidence проходил декодер, но отклонялся assemble. Не заменяем отсутствующий
    источник выдуманной ссылкой; модель должна выбрать допустимый результат.
    """
    schema = AnswerReview.model_json_schema()
    selection = generation_schema()
    schema['$defs'].update(selection['$defs'])
    schema['$defs']['ModelSelection'] = {'oneOf': selection['oneOf']}
    return schema


def assemble(raw, chunks, request, *, demo=False, truncated=False, secret='', state=None) -> CoreResponse:
    """Для связного ответа обязательны существующие источники и точные цитаты.

    Совпадение цитат не гарантирует логическую достоверность всего пересказа.
    Ответы старых клиентов без поля answer собираем из цитат, сохраняя контракт.
    """
    state = state if state is not None else build_consultation_state(request)
    summary_requested = state.summary_requested
    user_texts = [request.message] + [turn.content for turn in request.history if turn.role == 'user']
    intent = state.intent if summary_requested else None
    excluded = intent.excluded if intent else ()
    summary_texts = list(dict.fromkeys(fact.quote for fact in state.facts)) if intent else user_texts
    if isinstance(raw, dict) and raw.get('summary_draft') is not None:
        draft = raw['summary_draft']
        verified = (isinstance(draft, str) and bool(draft.strip()) and len(draft) <= 3000
                    and all(not line.strip() or _summary_excerpt_matches(line.strip(), summary_texts)
                            for line in draft.splitlines()))
        # После разрешения поправок даже дословный черновик модели может
        # содержать только мощность и пропустить исправленный объект/задачу.
        # Восстанавливаем его из актуальных выдержек целиком.
        if (excluded or not summary_requested or not verified
                or summary_texts != user_texts[1:] + user_texts[:1]):
            # Неподтверждённый или незапрошенный черновик не стирает ответ.
            # При явной просьбе ниже восстановим исходные выдержки.
            raw = dict(raw, summary_draft=None)
    try:
        selected = ModelSelection.model_validate(raw)
    except ValidationError:
        raise ModelResponseError('Результат модели не соответствует контракту ядра') from None
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    by_id.update({f"D{i}": chunk for i, chunk in enumerate(chunks, 1)})
    sources = []
    lines = []
    seen = set()
    for item in selected.evidence:
        chunk = by_id.get(item.chunk_id)
        if chunk is None or (item.quote is None and not selected.answer):
            raise ModelResponseError('Не удалось подтвердить источник ответа', 'invalid_evidence')
        quote = item.quote.strip() if item.quote is not None else chunk.text
        if not quote:
            raise ModelResponseError('Не удалось подтвердить источник ответа', 'invalid_evidence')
        if quote not in chunk.text:
            # Модель может склеить переносы Markdown. Из источника возвращаем
            # исходную подстроку; числа, знаки и порядок слов должны совпасть.
            pattern = r'\s+'.join(re.escape(word) for word in quote.split())
            match = re.search(pattern, chunk.text)
            if match is None:
                raise ModelResponseError('Не удалось подтвердить источник ответа', 'invalid_evidence')
            quote = match.group()
        if chunk.chunk_id in seen:
            raise ModelResponseError('Модель повторила источник вместо самостоятельного основания')
        seen.add(chunk.chunk_id)
        # Название и положение берём из индекса, а не из непроверенного ответа модели.
        source = Source(document_id=chunk.document_id, chunk_id=chunk.chunk_id,
                        title=chunk.title, excerpt=quote, section=chunk.section, page=chunk.page)
        sources.append(source)
        location = f', стр. {chunk.page}' if chunk.page else (f', {chunk.section}' if chunk.section else '')
        lines.append(f'«{quote}»\nИсточник [{len(sources)}]: {chunk.title}{location}.')
    grounding_adjusted = False
    if selected.status == 'answered':
        answer = (selected.answer.strip() if selected.answer is not None
                  else 'В материалах базы найдено:\n\n' + '\n\n'.join(lines))
        # Вопрос может находиться перед пояснением. Подпись интерфейса
        # при этом не считается вопросом или просьбой к клиенту.
        step_text = INTERFACE_LABEL.sub('', answer)
        had_question = '?' in step_text
        photo_docs = {s.document_id for s in sources if 'распознавание показаний' in s.title.lower()}
        commercial_verification = re.search(
            r'(?:коммерческ\w*\s+уч[её]т|начислен\w*)[^.!?\n]{0,180}'
            r'после\s+(?:обязательн\w*\s+)?верификац|'
            r'после\s+верификац[^.!?\n]{0,180}(?:коммерческ\w*\s+уч[её]т|начислен)', answer, re.I)
        hardware_question = (re.search(r'год|выпуск|серийн|штрих', request.message, re.I)
                             and not re.search(r'показан|коммерческ|начисл|точност', request.message, re.I))
        if (photo_docs and re.search(r'\bпоказан', answer, re.I) and not commercial_verification
                and (not hardware_question or re.search(r'коммерческ|начисл', answer, re.I))):
            # Ограничение коммерческого использования обязательно для показаний.
            # Проверка серийного номера не заменяет условие коммерческого учёта.
            # Добавляем только точный фрагмент того же продукта из текущего
            # контекста; собственный текст или другой документ не подставляем.
            condition = next((c for c in chunks if c.document_id in photo_docs
                              and re.match(r'Распознанное показание является дополнительным', c.text, re.I)
                              and re.search(r'после\s+верификац', c.text, re.I)), None)
            if condition:
                parts = answer.rsplit('\n\n', 1)
                if len(parts) == 2 and parts[1].rstrip().endswith('?'):
                    answer = parts[0] + '\n\n' + condition.text + '\n\n' + parts[1]
                else:
                    answer += '\n\n' + condition.text
                if condition.chunk_id not in seen:
                    sources.append(Source(document_id=condition.document_id, chunk_id=condition.chunk_id,
                        title=condition.title, excerpt=condition.text, section=condition.section, page=condition.page))
                    seen.add(condition.chunk_id)
        # Перечень данных не запрещает полезный следующий вопрос, но сам
        # по себе не оправдывает сбор произвольных параметров другого этапа.
        if (selected.question and not had_question
                and (not data_checklist_question(request.message)
                     or checklist_question_relevant(selected.question, request.message, sources))
                and not EMBEDDED_QUESTION.search(step_text) and selected.question.strip() not in step_text):
            answer += '\n\n' + selected.question.strip()
        def hide_aliases(match):
            # Метки D1 и далее нужны выбору источников внутри запроса.
            # Пользовательский код прибора с тем же обозначением имеет приоритет.
            codes = re.findall(r'D\d+', match.group())
            text = '\n'.join(user_texts)
            if all(code in by_id and not re.search(rf'\b{code}\b', text) for code in codes):
                return ''
            return match.group()
        answer = INLINE_ALIAS.sub(hide_aliases, answer).strip()
        if not answer:
            raise ModelResponseError('Ответ содержит только служебные метки')
        if not summary_requested:
            # Высказывания пользователя в заказанном резюме не переписываем.
            adjusted = grounded_free_work(answer, [s.excerpt for s in sources])
            grounding_adjusted = adjusted != answer
            answer = adjusted
        action = 'answer'
    elif selected.status == 'clarification_needed':
        answer = selected.question.strip()
        action = 'clarify'
    elif selected.status == 'out_of_scope':
        answer = 'Я консультирую по продуктам Energytool и задачам их применения. Уточните вопрос в этих пределах.'
        action = 'out_of_scope'
    else:
        answer = ('В предоставленных материалах недостаточно подтверждённых сведений для ответа. '
                  'Можно уточнить задачу или обратиться к специалисту за недостающей информацией.')
        action = 'clarify'
    summary = selected.summary_draft
    if summary_requested and summary is None:
        # Собираем исходные выдержки без выдуманных контактов. Явные поправки
        # простых параметров уже разрешены в summary_excerpts; сложные случаи
        # сохраняют хронологию и требуют проверки пользователем. Длинные
        # выдержки пропускаем целиком, не обрезая фразу в середине.
        parts, size = [], 0
        candidates = []
        for text in summary_texts:
            if summary_control_message(text):
                continue
            if request.history and SUMMARY_REQUEST.search(text):
                # Отдельное предложение с поручением может следовать за фактом
                # в той же реплике. Сохраняем фактическую часть дословно.
                candidates.extend(sentence for sentence in re.split(r'(?<=[.!?;])\s+', text)
                                  if not SUMMARY_REQUEST.search(sentence)
                                  and not summary_control_message(sentence))
            else:
                candidates.append(text)
        for text in reversed(candidates):
            if size + len(text) + 1 <= 3000:
                parts.append(text)
                size += len(text) + 1
        summary = '\n'.join(reversed(parts)) or None
    if summary:
        # Черновик не должен добавлять контакт или обстоятельство из фантазии модели.
        if not summary_requested:
            raise CoreUnavailable('Резюме без запроса пользователя отклонено')
        if any(line.strip() and not any(line.strip() in text for text in user_texts)
               for line in summary.splitlines()):
            raise CoreUnavailable('Черновик содержит неподтверждённые сведения')
        # Конкурсный CSV содержит только answer. Явно заказанный черновик должен
        # быть виден и в нём; отдельное поле сохраняется для карточки backend.
        # Один заголовок «Резюме» не подтверждает наличие актуальных фактов.
        # CSV экспортирует answer, поэтому сохраняем в нём проверенный черновик.
        if summary.strip() not in answer and not _visible_summary_covers_draft(answer, summary):
            answer += '\n\nЧерновик по вашим сообщениям:\n' + summary.strip()
    warnings = []
    if grounding_adjusted:
        warnings.append('Неподтверждённая бесплатность физических работ убрана из ответа.')
    if truncated:
        warnings.append('Длинная история ограничена; для ранних обстоятельств может потребоваться повторное уточнение.')
    if demo:
        warnings.append('ДЕМОНСТРАЦИЯ: вымышленные документы и заглушка модели, не консультация по Energytool.')
        answer = 'Демонстрационный режим.\n\n' + answer
    result = CoreResponse(answer=answer, sources=sources, action=action, summary_draft=summary,
                          core_status=selected.status, missing_information=selected.missing_information,
                          warnings=warnings)
    if secret:
        values = result.model_dump()
        if redact_secret(values, secret) != values:
            raise CoreUnavailable('Ответ отклонён проверкой конфиденциальности')
    return result
