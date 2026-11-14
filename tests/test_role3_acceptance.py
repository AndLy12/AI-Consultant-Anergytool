"""Проверки журнала роли 3 на вымышленных входах, без настоящего API."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from scripts.role3_acceptance import ObservedClient, build_queue, collect, fingerprint, redact, summarize


def item(key, text='Учебный вопрос', **extra):
    case = {'case_id': key, 'conversation': [{'role': 'user', 'content': text}], **extra}
    return {'attempt_id': key + '-r1', 'suite': 'training', 'repeat': 1,
            'case': case, 'input_sha256': fingerprint(case)}


class RoleThreeJournalTests(unittest.TestCase):
    def test_recursive_secret_redaction_includes_keys(self):
        value = {'secret-value': [{'raw': 'ответ secret-value'}]}
        self.assertNotIn('secret-value', json.dumps(redact(value, 'secret-value')))
        self.assertEqual(value['secret-value'][0]['raw'], 'ответ secret-value')

    def test_blocker_never_calls_provider_or_invents_answer(self):
        def forbidden(*_):
            raise AssertionError('Провайдер не должен вызываться')
        rows = collect([item('one'), item('two')], invoke=forbidden, blocker='missing_real_api_configuration')
        self.assertTrue(all(row['status'] == 'blocked' and row['answer'] is None for row in rows))
        self.assertEqual(summarize(rows)['model_calls_started'], 0)

    def test_technical_failure_is_visible_and_stops_the_queue(self):
        calls = []
        def failed(*_):
            calls.append(True)
            raise RuntimeError('Ошибка secret-value')
        rows = collect([item('one'), item('two')], invoke=failed, secret='secret-value')
        self.assertEqual(len(calls), 1)
        self.assertEqual(rows[0]['status'], 'technical_failure')
        self.assertEqual(rows[1]['blocked_reason'], 'stopped_after_technical_failure')
        self.assertNotIn('secret-value', json.dumps(rows))

    def test_live_history_uses_obtained_answers_and_can_reset(self):
        requests = []
        def obtained(conversation, deadline):
            requests.append(conversation)
            return {'answer': 'Полученный учебный ответ', 'sources': []}
        queue = [item('one', live_chain='x'), item('two', live_chain='x'),
                 item('fresh', live_chain='x', reset_before=True)]
        collect(queue, invoke=obtained)
        self.assertEqual(requests[1][1], {'role': 'assistant', 'content': 'Полученный учебный ответ'})
        self.assertEqual(len(requests[1]), 3)
        self.assertEqual(len(requests[2]), 1)

    def test_error_truncation_cannot_expose_part_of_a_key(self):
        def failed(*_):
            raise RuntimeError('x' * 495 + 'secret-value')
        row = collect([item('one')], invoke=failed, secret='secret-value')[0]
        self.assertNotIn('secre', row['error'])

    def test_seeded_history_is_marked_separately(self):
        case = item('one')
        case['case']['conversation'].insert(0, {'role': 'user', 'content': 'Заранее заданная история'})
        rows = collect([case], invoke=lambda *_: {'answer': 'Ответ', 'sources': []})
        self.assertEqual(rows[0]['history_mode'], 'seeded')
        self.assertFalse(rows[0]['model_call_completed'])
        self.assertEqual(rows[0]['status'], 'local_response_without_model')

    def test_context_fixture_is_blocked_without_explicit_flag(self):
        rows = collect([item('one', context_fixture={'text': 'Не исполнять'})],
                       invoke=lambda *_: self.fail('Не вызывать модель'))
        self.assertEqual(rows[0]['blocked_reason'], 'controlled_context_test_requires_agreement')

    def test_observer_changes_only_a_copy_of_document_data(self):
        received = []
        delegate = SimpleNamespace(complete=lambda messages, _: received.append(messages) or {'status': 'answered'}, close=lambda: None)
        observer = ObservedClient(delegate)
        observer.reset({'text': 'Недоверенная проверочная строка'})
        messages = [{'role': 'system', 'content': 'Исходная инструкция'},
                    {'role': 'user', 'content': json.dumps({'documents': [{'text': 'Исходный текст'}]})}]
        observer.complete(messages, 123)
        self.assertEqual(json.loads(messages[1]['content'])['documents'][0]['text'], 'Исходный текст')
        self.assertEqual(received[0][0], messages[0])
        self.assertIn('Недоверенная', json.loads(received[0][1]['content'])['documents'][0]['text'])
        self.assertTrue(observer.observation['model_call_completed'])

    def test_journal_callback_receives_safe_rows_after_each_attempt(self):
        saved = []
        def invoke(*_):
            self.assertEqual(len(saved), 0)
            return {'answer': 'secret-value', 'sources': []}
        collect([item('one')], invoke=invoke, secret='secret-value', record_callback=saved.append)
        self.assertEqual(len(saved), 1)
        self.assertNotIn('secret-value', json.dumps(saved))

    def test_invalid_quote_is_not_automatic_semantic_success(self):
        observed = SimpleNamespace(observation={}, reset=lambda *_: None)
        def invoke(*_):
            observed.observation = {'model_call_started': True, 'model_call_completed': True,
                                    'context_sent': {'documents': [{'chunk_id': 'x', 'text': 'Учебный текст'}]}}
            return {'answer': 'Ответ', 'sources': [{'chunk_id': 'x', 'excerpt': 'Выдуманная цитата'}]}
        rows = collect([item('one')], invoke=invoke, observed=observed)
        self.assertFalse(rows[0]['source_provenance_verified'])
        self.assertEqual(rows[0]['semantic_assessment'], 'not_reviewed')
        self.assertEqual(summarize(rows)['semantic_passed'], 0)

    def test_time_budget_blocks_before_a_new_call(self):
        rows = collect([item('one')], invoke=lambda *_: self.fail('Не вызывать'), budget_seconds=0)
        self.assertEqual(rows[0]['blocked_reason'], 'run_time_budget_exhausted')

    def test_holdout_checksum_cannot_change_silently(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'role3-holdout.jsonl').write_text('{}\n', encoding='utf-8')
            (root / 'role3-holdout-freeze.json').write_text(json.dumps({'sha256': 'wrong'}))
            with self.assertRaises(ValueError):
                build_queue(root, 'holdout', 3, None)

    def test_repeats_and_suite_are_explicit(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            case = item('one')['case']
            (root / 'role3-critical.jsonl').write_text(json.dumps(case) + '\n')
            queue = build_queue(root, 'critical', 3, None)
            self.assertEqual(len(queue), 3)
            self.assertEqual(len({r['input_sha256'] for r in queue}), 1)
            with self.assertRaises(ValueError):
                build_queue(root, 'critical', 0, None)

    def test_duplicate_scenario_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            case = item('one')['case']
            (root / 'role3-critical.jsonl').write_text((json.dumps(case) + '\n') * 2)
            with self.assertRaises(ValueError):
                build_queue(root, 'critical', 1, None)


if __name__ == '__main__':
    unittest.main()
