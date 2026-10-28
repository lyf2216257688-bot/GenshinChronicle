"""Provider-free fixtures. No corpus/model results and no human gold."""
import ast
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from genshin_corpus.retrieval import navi_f2_preflight as f2
from tests.retrieval.test_navi_v02_contract import fixture, proposition


ROOT = Path(__file__).resolve().parents[2]


def view(mode='generation', arm='rc1'):
    value = {s: {'title': '合成'+s, 'snippets': [{'id': a, 'text': '合成原文'} for a in ids],
                 'coverage': {'whole_page_coverage': False}} for s, ids in [('A', ['a1', 'a2']), ('B', ['b1', 'b2'])]}
    if mode == 'review':
        if arm == 'rc1':
            value['candidates'] = [{k: v for k, v in fixture()[2][0].items() if k != 'historical_grade'}]
        else: value['candidate'] = {'candidate_id': 'c1', 'proposition': '合成原命题', 'interpretation_boundary': '原句非权威'}
    return value


def case(mode='generation', arm='rc1'):
    return f2.compile_case({'pair_id': 'pair', 'sources': ['1', '2'],
                           'cohort': 'GENERATION' if mode == 'generation' else 'AUDIT'},
                          arm, view(mode, arm), {'native_prompt': '合成JSON请求'}, 'input-hash', 'schema-hash')


def envelope(text, finish='stop', model='fixture-model'):
    return json.dumps({'model': model, 'usage': {'input_tokens': 17, 'output_tokens': 8},
        'choices': [{'finish_reason': finish, 'message': {'role': 'assistant', 'content': text}}]}).encode()


class F2PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        segments = []
        for file, name in [('src/genshin_corpus/rag/navi_r1_qa.py', 'validate'),
                           ('src/genshin_corpus/rag/navi_g1_experiment.py', 'validate_new_review')]:
            source = (ROOT/file).read_text(encoding='utf-8')
            node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
            segments.append(ast.get_source_segment(source, node))
        (self.root/'legacy-native-validators.py').write_text('\n\n'.join(segments), encoding='utf-8')
        self.native = f2.native_legacy_validator(self.root)
        self.schema = f2.read(ROOT/'docs/research/phase-05/p05-navi-b-v02.schema.json')
        self.packet = {'sides': {'A': '1', 'B': '2'}, 'aliases': {'A001': 'qa', 'B001': 'qb'}}
        self.evidence = {k: {'source': s, 'text': '原文', 'text_sha256': sha256('原文'.encode()).hexdigest(),
                            'official_unit_ids': ['ru-'+k]} for k, s in [('qa', '1'), ('qb', '2')]}

    def legacy(self):
        return {'a_ids': ['A001'], 'b_ids': ['B001'], 'verdict': 'reject', 'relation': 'none',
            'claim_level': 'textual', 'explanation': '', 'increment_a_from_b': '', 'increment_b_from_a': '',
            'counterevidence': '', 'proposed_claim': '', 'literary_value': False, 'unsupported_identity_or_cause': False,
            'endpoint_roles': {'A': 'game_text', 'B': 'game_text'}, 'role_basis': {'A': '依据', 'B': '依据'},
            'anchored_correspondences': [], 'grade_recommendation': 'D', 'self_check': ''}

    def validate(self, value, c=None):
        return f2.validate_content(json.dumps(value, ensure_ascii=False), c or case(), self.packet,
                                    self.evidence, self.schema, self.native)

    def test_no_history_or_probe_metadata_allowed_in_view(self):
        for key in f2.FORBIDDEN_INPUT:
            v = view(); v['A'][key] = 'leak'
            with self.subTest(key=key), self.assertRaises(ValueError): f2.check_view(v, 'generation', 'rc1')

    def test_original_hash_and_input_envelope_not_silently_fixed(self):
        v = view('review'); v['candidates'][0]['text'] += 'changed'
        with self.assertRaises(ValueError): f2.check_view(v, 'review', 'rc1')
        v = view('review', 'legacy'); v['candidate']['grade_recommendation'] = 'B'
        with self.assertRaises(ValueError): f2.check_view(v, 'review', 'legacy')

    def test_two_messages_no_other_case_and_deterministic_hash(self):
        a, b = case(), case()
        self.assertEqual(a, b); self.assertEqual([m['role'] for m in a['messages']], ['system', 'user'])
        self.assertFalse(a['send_authorized']); self.assertIsNone(a['model'])

    def test_order_permutation_stability_pair_adjacency_and_balanced_first_arm(self):
        pairs = [{'pair_id': str(i), 'cohort': 'GENERATION' if i < 16 else 'AUDIT'} for i in range(24)]
        order = f2.request_order(pairs, 'seed')
        self.assertEqual(order, f2.request_order(list(reversed(pairs)), 'seed'))
        first = {'GENERATION': [], 'AUDIT': []}
        for i in range(0, 48, 2):
            pid, arm = order[i].rsplit('-', 1)
            self.assertTrue(order[i+1].startswith(pid+'-'))
            first['GENERATION' if int(pid) < 16 else 'AUDIT'].append(arm)
        self.assertEqual(first['GENERATION'].count('legacy'), 8)
        self.assertEqual(first['AUDIT'].count('legacy'), 4)

    def test_full_rc1_template_and_schema_in_prompt(self):
        folder = ROOT/'data/retrieval'  # no full corpus access; use only checked-in docs
        snapshot = self.root/'snapshot'; snapshot.mkdir()
        for name in ['p05-navi-b-prompts-v02.md', 'p05-navi-b-v02.schema.json']:
            (snapshot/name).write_bytes((ROOT/'docs/research/phase-05'/name).read_bytes())
        parts = f2.prompt_parts(snapshot, 'rc1', 'review')
        self.assertEqual(json.loads(parts['complete_schema'].split('\n')[-1]), self.schema)
        self.assertIn('COUNTERFACTUAL_GUARD', ''.join(parts.values()))
        self.assertIn('原text不变', parts['native_prompt'])

    def test_render_requires_explicit_capability_and_parameters(self):
        with self.assertRaises(ValueError): f2.render_wire(case(), {})
        cfg = {'model': 'fixture-model', 'route': 'fixture-only', 'api_surface': 'responses',
               'max_output_tokens': 100, 'generation_parameters': {}, 'capabilities_confirmed': True}
        a = f2.render_wire(case(), cfg)
        self.assertFalse(a['send_authorized']); self.assertNotIn('previous_response_id', a['wire'])
        cfg['api_surface'] = 'chat_completions'; b = f2.render_wire(case(), cfg)
        self.assertEqual(b['wire']['messages'], case()['messages'])
        cfg['generation_parameters']['conversation'] = 'old'
        with self.assertRaises(ValueError): f2.render_wire(case(), cfg)

    def test_message_tampering_rejected(self):
        c = case(); c['messages'][0]['content'] = 'changed'
        with self.assertRaises(ValueError): f2.render_wire(c, {})

    def test_metrics_have_no_fake_actual_tokens_or_prices(self):
        m = f2.measure(case(), {'native_prompt': '合成JSON请求'})
        self.assertIsNone(m['actual_model_tokens']); self.assertIsNone(m['api_framing_tokens'])
        self.assertGreaterEqual(m['proxy_bytes_div1'], m['proxy_bytes_div2'])

    def test_unknown_budget_and_cached_reasoning_not_double_charged(self):
        self.assertEqual(f2.priced_cost({}, {}, reasoning_in_output=None)['status'], 'UNKNOWN')
        t = {'input': 1000000, 'cached_input': 250000, 'output': 100000, 'reasoning': 50000, 'tool_calls': 0}
        p = {'P_in': 4, 'P_cache': 1, 'P_out': 10, 'P_reason': 20, 'P_tool': 0, 'other_fees': 0}
        self.assertAlmostEqual(f2.priced_cost(t, p, reasoning_in_output=True)['amount'], 4.25)
        self.assertAlmostEqual(f2.priced_cost(t, p, reasoning_in_output=False)['amount'], 5.25)
        t['cached_input'] = 2000000
        with self.assertRaises(ValueError): f2.priced_cost(t, p, reasoning_in_output=True)

    def test_legacy_native_complete_and_reject_is_not_format_failure(self):
        self.assertEqual(self.validate(self.legacy(), case(arm='legacy'))['status'], 'VALIDATED_MECHANICAL_ONLY')
        v = self.legacy(); v.pop('role_basis')
        self.assertEqual(self.validate(v, case(arm='legacy'))['status'], 'LEGACY_NATIVE_CONTRACT_FAILURE')

    def test_legacy_bad_endpoint_or_quote_is_preserved_as_failure(self):
        for key, bad in [('a_ids', ['B001']), ('quote', 'invented')]:
            v = self.legacy(); v[key] = bad
            self.assertEqual(self.validate(v, case(arm='legacy'))['status'], 'LEGACY_NATIVE_CONTRACT_FAILURE')

    def test_zero_candidates_and_U_are_distinct_from_execution_failure(self):
        v, _, _ = fixture('generation'); v.update(propositions=[], decision='NO_FORMAL_NAVI', no_link_reason='合成无候选')
        self.assertEqual(self.validate(v)['status'], 'VALIDATED_MECHANICAL_ONLY')
        p = proposition('u', grade='U', release='UNRESOLVED'); p['b_ids'] = []
        v.update(propositions=[p], decision='UNRESOLVED', no_link_reason='')
        self.assertEqual(self.validate(v)['status'], 'VALIDATED_MECHANICAL_ONLY')

    def test_generation_component_role_does_not_claim_review_discovery(self):
        v, _, _ = fixture('generation'); v['propositions'][0]['role'] = 'ORIGINAL_COMPONENT'
        self.assertEqual(self.validate(v)['status'], 'VALIDATED_MECHANICAL_ONLY')
        v['propositions'][0]['role'] = 'NEW_DISCOVERY'
        self.assertEqual(self.validate(v)['status'], 'CONTEXT_CONTRACT_FAILURE')

    def test_schema_and_context_failures_are_separate(self):
        v, _, _ = fixture('generation'); v['propositions'][0].pop('evidence_gap')
        self.assertEqual(self.validate(v)['status'], 'SCHEMA_FAILURE')
        v, _, _ = fixture('generation'); v['propositions'][0]['a_ids'] = ['b1']
        self.assertEqual(self.validate(v)['status'], 'CONTEXT_CONTRACT_FAILURE')

    def test_original_cannot_be_washed_by_valid_safe_rewrite(self):
        v, _, _ = fixture(); self.assertEqual(self.validate(v, case('review'))['status'], 'VALIDATED_MECHANICAL_ONLY')
        v['propositions'].pop(0)
        self.assertEqual(self.validate(v, case('review'))['status'], 'CONTEXT_CONTRACT_FAILURE')

    def test_strict_json_no_fences_duplicate_keys_or_truncation_repair(self):
        for text in ['```json\n{}\n```', '{"a":1,"a":2}', '{"a":', '{"a":NaN}']:
            self.assertEqual(f2.validate_content(text, case(), {}, {}, self.schema, self.native)['status'], 'JSON_PARSE_FAILURE')

    def test_truncated_even_complete_looking_json_not_success(self):
        self.assertEqual(f2.decode_response(envelope('{}', finish='length'), 'chat_completions')['status'], 'OUTPUT_TRUNCATED')
        raw = json.dumps({'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}).encode()
        self.assertEqual(f2.decode_response(raw, 'responses')['status'], 'OUTPUT_TRUNCATED')

    def test_responses_envelope_reasoning_and_usage_preserved(self):
        raw = json.dumps({'status': 'completed', 'model': 'fixture-model',
            'usage': {'output_tokens_details': {'reasoning_tokens': 5}}, 'output': [
                {'type': 'reasoning', 'summary': []}, {'type': 'message', 'role': 'assistant',
                 'content': [{'type': 'output_text', 'text': '{}'}]}]}).encode()
        d = f2.decode_response(raw, 'responses')
        self.assertEqual(d['status'], 'CONTENT'); self.assertEqual(d['usage']['output_tokens_details']['reasoning_tokens'], 5)

    def test_unknown_rejected_context_and_model_identity_not_auto_retried(self):
        kwargs = dict(case=case(), packet={}, evidence={}, schema=self.schema, legacy_validator=self.native,
                      surface='chat_completions')
        x = f2.analyze_response(None, **kwargs, execution_state='EXECUTION_UNKNOWN')
        self.assertEqual(x['usage'], 'UNKNOWN'); self.assertFalse(x['retry_allowed'])
        raw = b'{"error":{"code":"context_length_exceeded"}}'
        self.assertEqual(f2.analyze_response(raw, **kwargs, execution_state='RESPONSE_RECEIVED', http_status=400)['status'], 'CONTEXT_LIMIT')
        self.assertEqual(f2.analyze_response(envelope('{}'), **kwargs, execution_state='RESPONSE_RECEIVED', expected_model='other')['status'], 'MODEL_IDENTITY_MISMATCH')

    def test_raw_saved_before_failure_and_same_attempt_never_overwritten(self):
        raw = b'{broken'; meta = {'authority': 'SYNTHETIC_ONLY_NOT_F2', 'usage': 'UNKNOWN'}
        result = f2.record_attempt(self.root, 'fixture', case(), b'request', raw, meta,
                                  lambda x: f2.decode_response(x, 'chat_completions'))
        self.assertEqual(result['status'], 'RESPONSE_ENVELOPE_FAILURE')
        self.assertEqual((self.root/'attempts/fixture/response.bin').read_bytes(), raw)
        with self.assertRaises(FileExistsError):
            f2.record_attempt(self.root, 'fixture', case(), b'new', b'new', meta, lambda x: {})

    def test_validator_crash_does_not_destroy_raw(self):
        def crash(raw): raise RuntimeError('synthetic crash')
        r = f2.record_attempt(self.root, 'crash', case(), b'req', b'raw', {'authority': 'SYNTHETIC_ONLY'}, crash)
        self.assertEqual(r['status'], 'LOCAL_PROCESSING_FAILURE')
        self.assertEqual((self.root/'attempts/crash/response.bin').read_bytes(), b'raw')

    def test_no_transport_credentials_or_embedding_imports(self):
        source = Path(f2.__file__).read_text(encoding='utf-8')
        modules = []
        for n in ast.walk(ast.parse(source)):
            if isinstance(n, ast.Import): modules += [a.name for a in n.names]
            if isinstance(n, ast.ImportFrom): modules.append(n.module or '')
        self.assertFalse(set(modules) & {'openai', 'httpx', 'requests', 'socket', 'os', 'numpy', 'transformers'})


if __name__ == '__main__': unittest.main()
