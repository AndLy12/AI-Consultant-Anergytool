"""Проверка существования цитат и сборка ответа без свободных продуктовых утверждений."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from core_contract import CoreResponse, CoreUnavailable, Source


class Evidence(BaseModel):
    """Модель выбирает фрагмент, но не задаёт его название и координаты."""
    model_config = ConfigDict(extra='forbid', strict=True)
    chunk_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=3500)


class ModelSelection(BaseModel):
    """Схема не даёт модели добавить команды backend или неоговорённые поля."""
    model_config = ConfigDict(extra='forbid', strict=True)
    status: Literal['answered', 'clarification_needed', 'insufficient_data', 'out_of_scope']
    evidence: list[Evidence] = Field(default_factory=list, max_length=3)
    question: str | None = Field(default=None, max_length=300)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    summary_draft: str | None = Field(default=None, max_length=3000)

    @model_validator(mode='after')
    def consistency(self):
        if (self.status == 'answered') != bool(self.evidence):
            raise ValueError('Основания не соответствуют статусу')
        if self.status == 'clarification_needed' and (not self.question or not self.question.strip()):
            raise ValueError('Нет уточняющего вопроса')
        if self.status != 'clarification_needed' and self.question is not None:
            raise ValueError('Лишний уточняющий вопрос')
        if sum(len(e.quote) for e in self.evidence) > 3500:
            raise ValueError('Цитаты слишком длинные')
        if any(not value.strip() or len(value) > 300 for value in self.missing_information):
            raise ValueError('Некорректное описание недостающих сведений')
        return self


def assemble(raw, chunks, request, *, demo=False, truncated=False, secret='') -> CoreResponse:
    """Точная цитата доказывает происхождение текста, но не полноту ответа на вопрос.

    Полезность выбора источников и полноту условий проверяем на размеченном наборе.
    Свободный пересказ до таких измерений не выдаём за проверенный факт.
    """
    try:
        selected = ModelSelection.model_validate(raw)
    except ValidationError:
        raise CoreUnavailable('Результат модели не соответствует контракту ядра') from None
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    sources = []
    lines = []
    seen = set()
    for item in selected.evidence:
        chunk = by_id.get(item.chunk_id)
        if chunk is None or not item.quote.strip() or item.quote not in chunk.text:
            raise CoreUnavailable('Не удалось подтвердить источник ответа')
        if item.chunk_id in seen:
            raise CoreUnavailable('Модель повторила источник вместо самостоятельного основания')
        seen.add(item.chunk_id)
        # Название и положение берём из индекса, а не из непроверенного ответа модели.
        source = Source(document_id=chunk.document_id, chunk_id=chunk.chunk_id,
                        title=chunk.title, excerpt=item.quote, section=chunk.section, page=chunk.page)
        sources.append(source)
        location = f', стр. {chunk.page}' if chunk.page else (f', {chunk.section}' if chunk.section else '')
        lines.append(f'«{item.quote}»\nИсточник [{len(sources)}]: {chunk.title}{location}.')
    if selected.status == 'answered':
        answer = 'В материалах базы найдено:\n\n' + '\n\n'.join(lines)
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
    if summary:
        # Черновик не должен добавлять контакт или обстоятельство из фантазии модели.
        user_texts = [request.message] + [turn.content for turn in request.history if turn.role == 'user']
        if not any(word in request.message.lower() for word in ('заявк', 'резюме', 'подытож', 'итог')):
            raise CoreUnavailable('Резюме без запроса пользователя отклонено')
        if any(line.strip() and not any(line.strip() in text for text in user_texts)
               for line in summary.splitlines()):
            raise CoreUnavailable('Черновик содержит неподтверждённые сведения')
    warnings = []
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
