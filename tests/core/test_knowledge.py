"""Регрессии поиска по структуре базы; все тексты вымышленные."""

from dataclasses import replace

from core.config import Settings
from core.documents import make_chunks
from core.retrieval import Hit, SearchIndex
from scripts.check_core_knowledge import check


def test_markdown_separators_cannot_rank_using_heading():
    text = '# Экспорт отчёта\n\n---\n\n| Формат | Доступность |\n| --- | --- |\n| CSV | После проверки |\n\n***'
    chunks = tuple(make_chunks('report.md', [(text, None, None)], 1800))
    assert len(chunks) == 1
    assert 'CSV' in chunks[0].text
    assert chunks[0].title == 'Экспорт отчёта'
    assert chunks[0].text == text.strip()[chunks[0].start:chunks[0].end]
    separator = replace(chunks[0], text='---', chunk_id='separator')
    index = SearchIndex((separator, *chunks))
    assert all(hit.chunk.chunk_id != 'separator' for hit in index.search('экспорт'))
    assert SearchIndex((separator,)).search('экспорт') == ()


def test_context_restores_parent_section_and_separate_limits():
    text = ('# Вымышленный стенд\n\n## Возможности\n\nЭкспорт CSV.\n\n'
            '### Предварительная проверка\n\nТребуется разрешение оператора.\n\n'
            'После проверки выгрузка доступна.\n\n'
            '## Что мы не делаем\n\nНе выгружаем закрытые данные.\n\n'
            '## Другая возможность\n\nГрафики по дням.')
    chunks = tuple(make_chunks('report.md', [(text, None, None)], 1800))
    other = tuple(make_chunks('other.md', [('# Другое\n\nЗакрытые данные из чужого продукта.', None, None)], 1800))
    index = SearchIndex(chunks + other)
    context = index.context((Hit(chunks[0], 1),), 2000)
    texts = [c.text for c in context]
    assert texts == ['Экспорт CSV.', 'Требуется разрешение оператора.',
                     'После проверки выгрузка доступна.', 'Не выгружаем закрытые данные.']
    assert all(c.document_id == chunks[0].document_id for c in context)
    assert chunks[1].section_path == ('Вымышленный стенд', 'Возможности', 'Предварительная проверка')
    assert context == index.context((Hit(chunks[0], 1),), 2000)


def test_context_budget_never_cuts_a_guard_section_in_half():
    text = ('# Стенд\n\n## Экспорт\n\nCSV.\n\n'
            '## Ограничения\n\nТолько после проверки.\n\nИ только в учебной среде.')
    chunks = tuple(make_chunks('report.md', [(text, None, None)], 1800))
    index = SearchIndex(chunks)
    context = index.context((Hit(chunks[0], 1),), len(chunks[0].text) + len(chunks[1].text))
    assert context == (chunks[0],)
    assert sum(len(c.text) for c in context) <= len(chunks[0].text) + len(chunks[1].text)


def test_platform_common_chunker_uses_supported_sections(monkeypatch):
    monkeypatch.setenv('CORE_DEMO', 'true')
    monkeypatch.setenv('CHUNKER', 'common')
    assert Settings.from_environment().demo


def test_inflected_short_nouns_and_adjectives_find_the_source():
    chunks = tuple(make_chunks('training.md', [('# Учебный стенд\n\n'
        'Солнечные панели размещаются на крыше.\n\nТариф учебного стенда обсуждается отдельно.', None, None)], 1800))
    hits = SearchIndex(chunks).search('Солнечную панель допускает крыша?', 1)
    assert len(hits) == 1 and 'крыше' in hits[0].chunk.text


def test_offline_check_uses_only_corpus_and_parses_independent_dialogues(tmp_path):
    sources = tmp_path / 'knowledge-base'
    sources.mkdir()
    (sources / 'training.md').write_text('# Стенд\n\nЭкспорт CSV доступен.', encoding='utf-8')
    # Ответы и служебные материалы рядом с корпусом никогда не индексируются.
    (tmp_path / 'demo_output.csv').write_text('id,answer\n1,Секретный эталон', encoding='utf-8')
    questions = tmp_path / 'questions.csv'
    questions.write_text('id,question\n001,"Пользователь: Стенд\nАссистент: Уточните\n'
                         'Пользователь: Экспорт CSV?"\n002,Кулинарный рецепт\n', encoding='utf-8')
    report = check(tmp_path, questions)
    assert report['documents'] == 1
    assert report['questions'] == 2 and report['dialogue_questions'] == 1
    assert report['cases'][0]['id'] == '001' and report['cases'][0]['history_turns'] == 2
    assert report['questions_with_context'] == 1
    assert report['llm_called'] is False and report['generated_answers_evaluated'] is False
