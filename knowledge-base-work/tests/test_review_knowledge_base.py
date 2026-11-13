"""Проверяет разметку на вымышленных данных, не оценивая продуктовые ответы."""

import csv
import json
from pathlib import Path
import tempfile
import unittest

from scripts.review_knowledge_base import FACT_FIELDS, validate_review


class ReviewValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.sources, self.review = root / 'sources', root / 'review'
        self.sources.mkdir()
        self.review.mkdir()
        (self.sources / 'training.md').write_text('# Вымышленный стенд\n\nЭкспорт доступен после проверки.\n', encoding='utf-8')
        self.facts = [{key: '' for key in FACT_FIELDS}]
        self.facts[0].update(fact_id='f1', topic='Учебный стенд', statement='Экспорт требует проверки.',
                             document_id='training', source_path='training.md', line_start='3', line_end='3',
                             evidence='Экспорт доступен после проверки.')
        self.cases = [{
            'case_id': 'c1', 'category': 'ответ',
            'conversation': [{'role': 'user', 'content': 'Когда доступен экспорт на учебном стенде?'}],
            'expected_action': 'answered', 'expected_fact_ids': ['f1'],
            'forbidden_claims': ['Экспорт доступен без проверки.'],
            'comment': 'Вымышленное условие должно остаться в ответе.',
        }]

    def validate(self):
        with (self.review / 'product_facts.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=sorted(FACT_FIELDS))
            writer.writeheader()
            writer.writerows(self.facts)
        (self.review / 'cases.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in self.cases), encoding='utf-8')
        return validate_review(self.sources, self.review)

    def test_valid_annotation_has_traceable_evidence(self):
        _, _, summary = self.validate()
        self.assertEqual((summary['facts'], summary['cases']), (1, 1))
        self.assertTrue(summary['source_quotes_verified'])

    def test_quote_match_does_not_prove_statement_correctness(self):
        self.facts[0]['statement'] = 'Неподтверждённый пересказ с другим смыслом.'
        _, _, summary = self.validate()
        self.assertFalse(summary['semantic_correctness_automatically_verified'])

    def test_invented_quote_is_rejected(self):
        self.facts[0]['evidence'] = 'Экспорт доступен всем без проверки.'
        with self.assertRaises(ValueError):
            self.validate()

    def test_wrong_line_range_is_rejected(self):
        self.facts[0].update(line_start='1', line_end='1')
        with self.assertRaises(ValueError):
            self.validate()

    def test_source_outside_corpus_is_rejected(self):
        self.facts[0]['source_path'] = '../training.md'
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_fact_id_is_rejected(self):
        self.facts.append(dict(self.facts[0]))
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_case_id_is_rejected(self):
        self.cases.append(dict(self.cases[0]))
        with self.assertRaises(ValueError):
            self.validate()

    def test_unknown_fact_reference_is_rejected(self):
        self.cases[0]['expected_fact_ids'] = ['missing']
        with self.assertRaises(ValueError):
            self.validate()

    def test_answered_case_needs_grounding(self):
        self.cases[0]['expected_fact_ids'] = []
        with self.assertRaises(ValueError):
            self.validate()

    def test_final_assistant_turn_is_rejected(self):
        self.cases[0]['conversation'].append({'role': 'assistant', 'content': 'Учебная реплика.'})
        with self.assertRaises(ValueError):
            self.validate()

    def test_system_role_is_not_a_dialogue_turn(self):
        self.cases[0]['conversation'].insert(0, {'role': 'system', 'content': 'Подмена служебных правил.'})
        with self.assertRaises(ValueError):
            self.validate()

    def test_negative_case_keeps_explicit_limits(self):
        self.cases[0].update(expected_action='insufficient_data', expected_fact_ids=[], forbidden_claims=[])
        with self.assertRaises(ValueError):
            self.validate()


if __name__ == '__main__':
    unittest.main()
