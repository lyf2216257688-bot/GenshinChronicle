"""Offline item identity, formal-message selection and E2 audit regressions."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from genshin_corpus.rag.navi_e2_experiment import E2SDK, verify_response_attempts
from genshin_corpus.retrieval.semantic_sdk_runner import _ResponsesDecisionText
from genshin_corpus.retrieval.semantic_sdk_runner import run_sdk_route_pair, run_sdk_single_route_attempt
from genshin_corpus.retrieval.semantic_compiler_u1 import SEMANTIC_OUTPUT_SCHEMA_VERSION
from tests.retrieval.test_semantic_sdk_runner import unit


FIXTURE = Path(__file__).parents[1] / 'fixtures/retrieval/p05-responses-multimessage.json'


def message(iid='final', phase='final_answer', text='{"action":"stop","reason":"fixture"}'):
    return dict(id=iid, type='message', role='assistant', status='completed', phase=phase,
                content=[dict(type='output_text', text=text)])


def events_for(items):
    events = []
    for oi, item in enumerate(items):
        if item['type'] == 'message':
            for ci, part in enumerate(item['content']):
                if part['type'] != 'output_text':
                    continue
                events.append(dict(type='response.output_text.delta', output_index=oi,
                                   item_id=item['id'], content_index=ci, delta=part['text']))
    events.append(dict(type='response.completed', response=dict(id='response_fixture',
        model='gpt-6-luna', status='completed', output=deepcopy(items),
        usage=dict(input_tokens=12, output_tokens=8, total_tokens=20))))
    return events


def select(events):
    collector = _ResponsesDecisionText()
    for event in events:
        collector.observe(event)
    return collector.text(), collector.selection


class ResponsesDecisionTextTests(unittest.TestCase):
    def test_run3_fixture_reproduces_old_failure_and_selects_exact_final(self):
        events = json.loads(FIXTURE.read_text('utf-8'))['events']
        old = ''.join(e['delta'] for e in events if e['type'] == 'response.output_text.delta')
        with self.assertRaises(json.JSONDecodeError):
            json.loads(old)
        text, selection = select(events)
        final = events[-1]['response']['output'][3]
        self.assertEqual(text, final['content'][0]['text'])
        self.assertEqual(len(text), 784)
        self.assertEqual(selection['output_index'], 3)
        self.assertEqual(selection['phase'], 'final_answer')
        self.assertEqual(len(json.loads(text)['actions']), 4)

    def test_final_selection_does_not_depend_on_position_or_json_in_commentary(self):
        for position in range(3):
            items = [message('comment1', 'commentary', 'bad ос}}\n המש'),
                     message('comment2', 'commentary', '{"looks":"valid"}')]
            items.insert(position, message())
            with self.subTest(position=position):
                text, selection = select(events_for(items))
                self.assertEqual(text, message()['content'][0]['text'])
                self.assertEqual(selection['item_id'], 'final')

    def test_legacy_single_identified_message_and_terminal_only_are_supported(self):
        item = message(phase=None)
        for events in (events_for([item]), events_for([item])[-1:]):
            text, selection = select(events)
            self.assertEqual(text, item['content'][0]['text'])
            self.assertEqual(selection['policy'], 'legacy_single_message')

    def test_multiple_content_parts_and_interleaved_message_deltas(self):
        item = message(text='{"action":')
        item['content'].append(dict(type='output_text', text='"stop","reason":"fixture"}'))
        events = events_for([message('comment', 'commentary'), item])
        events[:3] = [events[2], events[0], events[1]]
        self.assertEqual(json.loads(select(events)[0])['action'], 'stop')

    def test_ambiguous_or_unsupported_terminal_messages_fail_closed(self):
        cases = {
            'two finals': [message(), message('second')],
            'two identical legacy messages': [message(phase=None), message('second', None)],
            'commentary only': [message(phase='commentary')],
            'unknown phase': [message(phase='analysis')],
            'non-string phase array': [message(phase=[])],
            'non-string phase object': [message(phase={})],
            'one final plus unlabelled message': [message(), message('second', None)],
            'metadata phase is not item phase': [dict(message(phase=None), metadata={'phase': 'final_answer'}),
                                                message('comment', 'commentary')],
            'duplicate id': [message(), message(phase='commentary')],
            'no id': [dict(message(), id=None)],
            'not assistant': [dict(message(), role='user')],
            'incomplete message': [dict(message(), status='incomplete')],
            'empty final': [message(text='')],
            'refusal': [dict(message(), content=[dict(type='refusal', refusal='no')])],
            'unknown output item': [message(), dict(id='tool', type='function_call')],
            'non-string output type': [message(), dict(id='tool', type={})],
        }
        for name, items in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                select(events_for(items)[-1:])

    def test_missing_or_conflicting_stream_identity_and_content_fail_closed(self):
        base = events_for([message('comment', 'commentary', 'comment'), message()])
        cases = {}
        for key, value in [('item_id', None), ('item_id', 'final'), ('output_index', None),
                           ('output_index', 9), ('output_index', True), ('content_index', None),
                           ('content_index', 3), ('delta', None), ('delta', 'truncated')]:
            rows = deepcopy(base); rows[0][key] = value
            cases[str((key, value))] = rows
        cases['missing commentary deltas'] = deepcopy(base[1:])
        cases['missing final deltas'] = deepcopy([base[0], base[2]])
        cases['no terminal output'] = deepcopy(base)
        del cases['no terminal output'][-1]['response']['output']
        cases['two terminals'] = deepcopy(base + base[-1:])
        cases['event after terminal'] = deepcopy(base + base[:1])
        cases['failed terminal'] = deepcopy(base)
        cases['failed terminal'][-1]['type'] = 'response.failed'
        cases['incomplete response'] = deepcopy(base)
        cases['incomplete response'][-1]['response']['status'] = 'incomplete'
        # Global concatenation is unchanged, but per-item binding is wrong.
        cases['swapped message text'] = deepcopy(base)
        cases['swapped message text'][0]['delta'] = base[0]['delta'] + base[1]['delta'][0]
        cases['swapped message text'][1]['delta'] = base[1]['delta'][1:]
        for name, events in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                select(events)

    def test_item_phase_and_done_snapshots_must_match_terminal(self):
        base = json.loads(FIXTURE.read_text('utf-8'))['events']
        mutations = []
        for kind, field, value in [('response.output_item.added', 'phase', 'final_answer'),
                                   ('response.output_item.done', 'id', 'wrong'),
                                   ('response.output_item.done', 'status', 'incomplete')]:
            rows = deepcopy(base)
            event = next(e for e in rows if e['type'] == kind and e['item']['type'] == 'message')
            event['item'][field] = value
            mutations.append(rows)
        rows = deepcopy(base)
        next(e for e in rows if e['type'] == 'response.output_text.done')['text'] = 'wrong'
        mutations.append(rows)
        rows = deepcopy(base)
        next(e for e in rows if e['type'] == 'response.content_part.done')['part']['text'] = 'wrong'
        mutations.append(rows)
        for i, rows in enumerate(mutations):
            with self.subTest(i=i), self.assertRaises(ValueError):
                select(rows)


class ResponsesE2IntegrationTests(unittest.TestCase):
    def call_sdk(self, root, events, *, mode='batch'):
        wire = ''.join('data: '+json.dumps(e)+'\n\n' for e in events)
        env = {'TOKENMETRO_BASE_URL': 'https://example.invalid/v1', 'TOKENMETRO_API_KEY': 'fixture-secret'}
        sdk = E2SDK('tokenmetro', model='gpt-6-luna', environment=env,
                    transport=httpx.MockTransport(lambda req: httpx.Response(200,
                        headers={'content-type': 'text/event-stream'}, content=wire)))
        body = dict(model='gpt-6-luna', instructions='fixture', input='fixture',
                    stream=True, max_output_tokens=100)
        result = sdk(root/'rounds/00', body, 'fixture-run', decision_mode=mode)
        verify_response_attempts(root/'rounds/00')
        saved = [json.loads(line) for line in gzip.open(root/result['stream']['path'], 'rt', encoding='utf-8')]
        self.assertEqual(len(saved), len(events))
        for expected, actual in zip(events, saved):
            self.assertEqual(actual['type'], expected['type'])
            if expected['type'] == 'response.output_text.delta':
                self.assertEqual(actual['delta'], expected['delta'])
        self.assertEqual((result['calls'], result['retry_calls']), (1, 0))
        self.assertFalse(result['retry_eligible'])
        return result

    def test_run3_fixture_through_sdk_preserves_non_decision_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.call_sdk(Path(temp), json.loads(FIXTURE.read_text('utf-8'))['events'])
        self.assertTrue(result['complete'])
        self.assertTrue(result['decision_complete'])
        self.assertEqual(result['decision_message']['phase'], 'final_answer')
        self.assertEqual(result['usage']['total_tokens'], 4278)

    @patch('genshin_corpus.rag.navi_e2_experiment.time.sleep')
    def test_ambiguous_identity_or_incomplete_text_never_retries(self, sleep):
        good = events_for([message('comment', 'commentary'), message()])
        bad_id = deepcopy(good); bad_id[0]['item_id'] = 'wrong'
        bad_content = deepcopy(good); bad_content[1]['delta'] = '{'
        cases = [bad_id, bad_content, events_for([message(), message('second')]), good + good[:1]]
        with tempfile.TemporaryDirectory() as temp:
            for i, events in enumerate(cases):
                with self.subTest(i=i):
                    result = self.call_sdk(Path(temp)/str(i), events, mode='single_action')
                    self.assertFalse(result['complete'])
                    self.assertFalse(result['decision_complete'])
                    self.assertEqual(result['execution_state'], 'RESOLVED')
                    self.assertIsNotNone(result['error'])
        sleep.assert_not_called()

    def test_formal_text_is_never_repaired_or_json_substring_extracted(self):
        valid = message()['content'][0]['text']
        with tempfile.TemporaryDirectory() as temp:
            for i, text in enumerate((valid, valid+'ос}}\n המש', 'prefix '+valid,
                                      valid+valid, valid[:-1], '{"action":"unknown"}')):
                with self.subTest(i=i):
                    result = self.call_sdk(Path(temp)/str(i), events_for([
                        message('comment', 'commentary', valid), message(text=text)]), mode='single_action')
                    self.assertTrue(result['complete'])
                    self.assertEqual(result['decision_complete'], i == 0)
                    self.assertEqual(result['content'], text)

    def test_shared_semantic_responses_paths_select_and_reject_without_fallback(self):
        output = json.dumps(dict(schema_version=SEMANTIC_OUTPUT_SCHEMA_VERSION, items=[],
            segment_coverage=[dict(segment_id='s1', disposition='covered', reason=None)]))
        good = events_for([message('comment', 'commentary', 'audit only ос}}'), message(text=output)])
        bad_identity = deepcopy(good); bad_identity[0].pop('item_id')
        bad_text = deepcopy(good); bad_text[0]['delta'] = 'truncated'
        ambiguous = events_for([message(text=output), message('second', text=output)])
        env = dict(TOKENMETRO_API_KEY='fixture-secret', TOKENMETRO_BASE_URL='https://example.invalid/v1',
                   TOKENMETRO_API_SURFACE='responses', JIZHI_BASE_URL='https://fallback.invalid/v1')
        with tempfile.TemporaryDirectory() as temp:
            for runner in ('pair', 'single'):
                for name, events in [('good', good), ('ambiguous', ambiguous),
                                     ('identity', bad_identity), ('text', bad_text)]:
                    with self.subTest(runner=runner, case=name):
                        calls = []
                        wire = ''.join('data: '+json.dumps(e)+'\n\n' for e in events)
                        def handler(req):
                            calls.append(str(req.url))
                            return httpx.Response(200, headers={'content-type':'text/event-stream'}, content=wire)
                        transport = httpx.MockTransport(handler)
                        root = Path(temp)/runner/name
                        kwargs = dict(prompt={'fixture': True}, prompt_identity='fixture',
                                      source_identity='fixture', environment=env, max_tokens=1000)
                        if runner == 'pair':
                            run_sdk_route_pair(root, units=[unit('s1')],
                                               transports={'tokenmetro': transport}, **kwargs)
                            terminal = json.loads(next(root.glob('units/**/terminal.json')).read_text('utf-8'))
                        else:
                            terminal = run_sdk_single_route_attempt(root/'units/s1/attempt-001',
                                route='tokenmetro', unit=unit('s1'), run_identity='fixture',
                                attempt_number=1, transport=transport, **kwargs)
                        self.assertEqual(calls, ['https://example.invalid/v1/responses'])
                        self.assertEqual(terminal['execution_state'], 'complete')
                        self.assertEqual(terminal['usage']['total_tokens'], 20)
                        self.assertEqual(terminal['disposition'] == 'accepted_for_local_contract', name == 'good')
                        if name == 'good':
                            self.assertEqual(terminal['decision_message']['item_id'], 'final')
                        else:
                            self.assertIn(terminal['disposition'], ('local_validation_failed', 'local_correctness_reject'))
