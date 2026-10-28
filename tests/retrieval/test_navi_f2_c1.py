"""Small synthetic fixtures only; no provider clients or corpus dependency."""
import ast
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest

from genshin_corpus.retrieval import navi_f2_c1 as c1
from tests.retrieval.test_navi_f2_preflight import case


def checker():
    path = Path(__file__).resolve().parents[2]/'src/genshin_corpus/rag/navi_r1_qa.py'
    source = path.read_text(encoding='utf-8')
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'extract_response_text')
    text = ast.get_source_segment(source, node)
    return c1.frozen_stream_checker(text, sha256(text.encode()).hexdigest())


def events():
    part = {'type': 'output_text', 'text': '{}'}
    item = {'id': 'm1', 'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [part]}
    response = {'id': 'r1', 'status': 'completed', 'model': c1.MODEL, 'output': [item],
                'usage': {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5}}
    identity = {'item_id': 'm1', 'output_index': 0, 'content_index': 0}
    return [{'type': 'response.created', 'response': {'id': 'r1'}},
            {'type': 'response.output_text.delta', **identity, 'delta': '{}'},
            {'type': 'response.output_text.done', **identity, 'text': '{}'},
            {'type': 'response.content_part.done', **identity, 'part': part},
            {'type': 'response.output_item.done', 'item': item},
            {'type': 'response.completed', 'response': response}]


class C1Tests(unittest.TestCase):
    def test_full_sse_roundtrip_and_preflight_projection(self):
        data = events()
        raw = ''.join('event: '+e['type']+'\r\ndata: '+json.dumps(e)+'\r\n\r\n' for e in data).encode()
        self.assertEqual(c1.sse_events(raw), data)
        self.assertEqual(c1.project_stream(data, checker())['text'], '{}')

    def test_stream_conflicts_and_missing_terminal_fail_closed(self):
        for mutation in ('delta', 'model', 'usage', 'missing', 'duplicate', 'identity'):
            data = deepcopy(events())
            if mutation == 'delta': data[1]['delta'] = 'bad'
            elif mutation == 'model': data[-1]['response']['model'] = 'another'
            elif mutation == 'usage': data[-1]['response']['usage']['total_tokens'] = 6
            elif mutation == 'missing': data.pop()
            elif mutation == 'duplicate': data.append(deepcopy(data[-1]))
            else: data[2]['item_id'] = 'wrong'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): c1.project_stream(data, checker())

    def test_sse_never_repairs_bad_frames(self):
        for raw in (b'data: {}', b'data: {}\n\n', b'event: wrong\ndata: {"type":"response.created"}\n\n',
                    b'data: [DONE]\n\ndata: {"type":"response.created"}\n\n',
                    b'data: {"type":"a","type":"b"}\n\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): c1.sse_events(raw)

    def test_draft_preserves_frozen_two_messages_and_cap(self):
        original = case(); copy = deepcopy(original)
        wire = c1.render_stream(original, 8192)
        self.assertEqual(original, copy)
        self.assertEqual(wire['input'], original['messages'][1]['content'])
        self.assertTrue(wire['stream'])
        self.assertFalse(original['send_authorized'])
        self.assertNotIn('temperature', wire)
        original['messages'][0]['content'] += 'changed'
        with self.assertRaises(ValueError): c1.render_stream(original, 8192)

    def test_empirical_proxy_is_not_measured_usage(self):
        self.assertEqual(c1.empirical_proxy(100, .251), 26)
        with self.assertRaises(ValueError): c1.empirical_proxy(100, 0)

    def test_missing_budget_blocks_and_unknown_is_reserved(self):
        self.assertEqual(c1.guard_budget(known_spend=1, unknown_reservations=2, next_reservation=3, hard_cap=None), 'BLOCKED_UNKNOWN_BUDGET')
        self.assertEqual(c1.guard_budget(known_spend=1, unknown_reservations=2, next_reservation=3, hard_cap=5), 'STOP_BEFORE_DISPATCH')

    def test_checker_integrity(self):
        with self.assertRaises(ValueError): c1.frozen_stream_checker('pass', 'invalid')
