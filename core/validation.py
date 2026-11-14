"""Проверка цитат и ответа модели; наличие оснований не доказывает весь пересказ."""

from typing import Annotated, Literal
import re
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from core_contract import CoreResponse, CoreUnavailable, ModelResponseError, Source


# Запрос на черновик распознаём по явному действию. Слово «итоговая»
# в вопросе о стоимости не означает просьбу подготовить заявку.
SUMMARY_REQUEST = re.compile(
    r'\b(?:подытож\w*|резюмиру\w*)\b|'
    r'\b(?:подготов\w*|состав\w*|собер\w*|сформир\w*|сдела\w*|оформ\w*|подвед\w*)'
    r'\b[^.!?\n]{0,80}\b(?:резюме|заявк\w*|итог(?:и)?)\b|'
    r'\b(?:можно|нужно|хочу)\s+(?:краткое\s+)?резюме\b', re.I)
SUMMARY_DECLINED = re.compile(
    r'\bне\s+(?:хочу|нужн\w*|надо)\s+(?:краткое\s+)?резюме\b|'
    r'\bне\s+(?:состав|оформ|подготов|дела)\w*[^.!?\n]{0,40}(?:резюме|заявк)|'
    r'\b(?:резюме|заявк\w*)\s+не\s+(?:нужн\w*|надо)', re.I)

INLINE_ALIAS = re.compile(r'\s*(?:\((D\d+(?:\s*,\s*D\d+)*)\)|\[(D\d+(?:\s*,\s*D\d+)*)\])')
EMBEDDED_QUESTION = re.compile(
    r'\b(?:уточните|подскажите|сообщите|укажите|назовите)\s*[,—:-]?\s*'
    r'(?:как\w*|сколько|есть\s+ли|где\b|что\b|тип\w*|модел\w*|материал\w*|'
    r'возраст\w*|площад\w*|регион\w*|мощност\w*|потреблен\w*|количеств\w*|'
    r'схем\w*|профил\w*|цел\w*|объ[её]м\w*)', re.I)
VISIBLE_SUMMARY = re.compile(
    r'\b(?:резюме|черновик|сводка|итог|итоги)(?:[ \t]+[^:.!?\n]{1,60})?[ \t]*[:—-]', re.I)
FREE_WORK = re.compile(
    r'\bбесплатн\w*\s+(?P<work>(?:(?:выездн\w*|энергетическ\w*|предварительн\w*)\s+)?'
    r'(?:обследован\w*|аудит\w*|монтаж\w*))', re.I)


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

    @field_validator('evidence', mode='before', json_schema_input_type=list[Evidence | str])
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
        return self


class AnswerReview(BaseModel):
    """Внутренняя проверка обязана вернуть окончательный объект консультации."""
    model_config = ConfigDict(extra='forbid', strict=True)
    issues: list[Annotated[str, Field(max_length=240)]] = Field(max_length=3)
    follow_up_question: Annotated[str, Field(min_length=3, max_length=300)] | None
    result: ModelSelection


def assemble(raw, chunks, request, *, demo=False, truncated=False, secret='') -> CoreResponse:
    """Для связного ответа обязательны существующие источники и точные цитаты.

    Совпадение цитат не гарантирует логическую достоверность всего пересказа.
    Ответы старых клиентов без поля answer собираем из цитат, сохраняя контракт.
    """
    summary_requested = bool(SUMMARY_REQUEST.search(request.message)
                             and not SUMMARY_DECLINED.search(request.message))
    user_texts = [request.message] + [turn.content for turn in request.history if turn.role == 'user']
    if isinstance(raw, dict) and raw.get('summary_draft') is not None:
        draft = raw['summary_draft']
        verified = (isinstance(draft, str) and bool(draft.strip()) and len(draft) <= 3000
                    and all(not line.strip() or any(line.strip() in text for text in user_texts)
                            for line in draft.splitlines()))
        if not summary_requested or not verified:
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
        had_question = answer.rstrip().endswith('?')
        photo_docs = {s.document_id for s in sources if 'распознавание показаний' in s.title.lower()}
        if photo_docs and re.search(r'\bпоказан', answer, re.I) and not re.search(r'верификац', answer, re.I):
            # Ограничение коммерческого использования обязательно для показаний.
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
        if (selected.question and not had_question
                and not EMBEDDED_QUESTION.search(answer) and selected.question.strip() not in answer):
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
        # Собираем целые реплики, не добавляя контактов и не обрезая фразу.
        # При длинном диалоге этот запасной черновик включает последние
        # помещающиеся сообщения и остаётся предметом проверки пользователя.
        parts, size = [], 0
        for text in reversed(user_texts[1:] or user_texts[:1]):
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
        # Уже сформулированное резюме может объединять исправления из истории.
        # Не дублируем его сырыми выдержками с прежними значениями параметров.
        if summary.strip() not in answer and not VISIBLE_SUMMARY.search(answer):
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
    if secret and secret in result.model_dump_json():
        raise CoreUnavailable('Ответ отклонён проверкой конфиденциальности')
    return result
