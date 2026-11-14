"""История одного запроса: разбор платформенной строки и ограничение контекста."""

import json
import re
from core_contract import CoreRequest, Turn

ROLE_LINE = re.compile(r'^\s*(user|пользователь|клиент|assistant|ассистент|консультант)\s*:\s*(.*)$', re.IGNORECASE)


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


def search_query(request: CoreRequest) -> str:
    """Короткая реплика вроде «а ограничения?» получает тему из последних user-реплик."""
    previous = [turn.content for turn in request.history if turn.role == 'user'][-3:]
    # Последний вопрос консультанта объясняет короткие ответы «четвёртая»
    # и «за два года». Он помогает найти тему, но не подтверждает факты о продукте.
    prompt = request.history[-1].content[-800:] if request.history and request.history[-1].role == 'assistant' else ''
    # Разговорное «проверите учёт» в материалах может называться аудитом.
    terms = ''
    if re.search(r'провер\w*.{0,28}уч[её]т|уч[её]т.{0,28}провер', request.message, re.I):
        terms = ' аудит существующей системы учёта модернизация приборов'
    # Поисковые синонимы связывают язык клиента с формулировкой документа.
    # Тексты ответа и источников остаются исходными.
    if re.search(r'фасад|на стен[еуы]', request.message, re.I):
        terms += ' монтаж на вертикальные поверхности зданий'
    if re.search(r'архив|суточн', request.message, re.I):
        terms += ' глубина архива разбивка день ночь идентификатор канала'
    return (request.message + '\n') * 3 + '\n'.join(previous)[-6000:] + '\n' + prompt + terms


def load_profile_question(text: str) -> bool:
    """Выделяем вопрос о времени нагрузки без потери второго явного запроса.

    Если клиент также спрашивает цену, диапазон мощности или размещение,
    сохраняем полный продуктовый контекст с соответствующими ограничениями.
    """
    return bool(re.search(r'вечер|ноч', text, re.I)) and not re.search(
        r'типов|диапазон|мощност|стоим|цен|тариф|кровл|скат|фасад|площад|'
        r'\b\d+\s*(?:квт|мвт|киловатт)', text, re.I)
