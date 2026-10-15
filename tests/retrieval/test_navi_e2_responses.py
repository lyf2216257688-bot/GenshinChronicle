"""Provider-free route, retry, uncertainty and real-probe harness contracts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from genshin_corpus.rag.navi_e2_experiment import E2SDK, _is_valid_decision_content
from genshin_corpus.rag.navi_e2_routes import E2_ROUTES
from genshin_corpus.rag.navi_e2_protocol_probe import run_probe, FIXTURE_UID, initialize_campaign, campaign_guard, initialize_followup_campaign
from genshin_corpus.retrieval.navi_exploration import ExplorationError


def decision():
    return {'actions': [
        {'action': 'search', 'query': 'missing catalogue', 'top_k': 1},
        {'action': 'read', 'unit_id': FIXTURE_UID, 'max_chars': 100, 'neighbors': 0},
        {'action': 'clue', 'text': 'Unverified hypothesis only.', 'source_unit_ids': [FIXTURE_UID]},
        {'action': 'stop', 'reason': 'Protocol test complete.'}],
        'memory': {'status': 'unverified_hypothesis_not_fact', 'text': 'Fixture only.'}, 'rationale': 'Protocol only.'}


def response(model='gpt-6-luna', *, usage=None, status='completed', event='response.completed', text=None):
    chunks = [
        {'type': 'response.reasoning_summary_text.delta', 'delta': 'summary'},
        {'type': 'response.output_text.delta', 'delta': json.dumps(decision()) if text is None else text},
        {'type': event, 'response': {'id': 'resp_fixture', 'model': model, 'status': status,
            'usage': {'input_tokens': 12, 'output_tokens': 8, 'total_tokens': 20} if usage is None else usage}},
    ]
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        content=''.join('event: '+c['type']+'\ndata: '+json.dumps(c)+'\n\n' for c in chunks))


class ResponsesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = {'TOKENMETRO_BASE_URL': 'https://example.invalid/v1', 'TOKENMETRO_API_KEY': 'fixture-secret',
            'NAVI_QWEN_BASE_URL': 'https://example.invalid/v1', 'NAVI_QWEN_API_KEY': 'qwen-secret',
            'JIZHI_BASE_URL': 'https://example.invalid/v1', 'JIZHI_API_KEY': 'jizhi-secret',
            'NAVI_GLM_BASE_URL':'https://example.invalid/v1','NAVI_GLM_API_KEY':'glm-secret'}
        self.body = {'model': 'gpt-6-luna', 'instructions': 'test', 'input': 'test', 'stream': True, 'max_output_tokens': 100}

    def tearDown(self):
        self.temp.cleanup()

    def sdk(self, handler, **kwargs):
        return E2SDK('tokenmetro', model='gpt-6-luna', environment=self.env,
                     transport=httpx.MockTransport(handler), **kwargs)

    def test_all_four_explicit_routes_and_fixture_bindings(self):
        for model, route in E2_ROUTES:
            with self.subTest(model=model):
                seen = []
                def handler(req):
                    seen.append(req)
                    self.assertEqual(req.url.path, '/v1/responses')
                    self.assertEqual(json.loads(req.content)['model'], model)
                    return response(model)
                report = run_probe(model=model, route=route, output_root=self.root/model,
                    transport=httpx.MockTransport(handler), environment=self.env)
                self.assertEqual(report['result'], 'PASS')
                self.assertEqual(report['tools_executed'], 0)
                expected = 'qwen-secret' if route == 'tokenmetro_qwen' else 'glm-secret' if route == 'jizhi_glm' else 'jizhi-secret' if route == 'jizhi' else 'fixture-secret'
                self.assertEqual(seen[0].headers['authorization'], 'Bearer '+expected)
                for path in (self.root/model).rglob('*'):
                    if path.is_file():
                        self.assertNotIn(expected.encode(), path.read_bytes())

    def test_missing_invalid_mismatched_selection_fails_before_dispatch(self):
        for model, route in [(None, None), ('gpt-6-luna', None), (None, 'tokenmetro'),
                             ('bad', 'tokenmetro'), ('deepseek-v4.1-flash', 'tokenmetro'),
                             ('qwen3.8-flash', 'tokenmetro')]:
            with self.subTest(model=model, route=route), self.assertRaises(ExplorationError):
                E2SDK(route, model=model, environment=self.env)

    def test_unconfirmed_url_and_missing_separate_qwen_key(self):
        env = dict(self.env, TOKENMETRO_BASE_URL='https://other.invalid/v1')
        with self.assertRaises(ExplorationError):
            E2SDK('tokenmetro', model='gpt-6-luna', environment=env)
        env = dict(self.env); env.pop('NAVI_QWEN_API_KEY')
        with self.assertRaises(ExplorationError):
            E2SDK('tokenmetro_qwen', model='qwen3.8-flash', environment=env, transport=httpx.MockTransport(lambda r: response()))

    def test_wrong_wire_model_and_old_protocol_rejected_without_evidence(self):
        sdk = self.sdk(lambda r: self.fail('must not dispatch'))
        for changes in ({'model': 'glm-5.3-flash'}, {'stream': False}, {'messages': []}, {'max_output_tokens': 8193}):
            with self.assertRaises(ExplorationError):
                sdk(self.root/'rounds/00', dict(self.body, **changes), 'run')
        self.assertFalse(list(self.root.rglob('issued.json')))

    @patch('genshin_corpus.rag.navi_e2_experiment.time.sleep')
    def test_exact_pre_generation_tpm_retry_stays_on_route_and_is_bounded(self, sleep):
        seen = []
        def handler(req):
            seen.append((str(req.url), req.content, req.headers['authorization']))
            if len(seen) < 3:
                return httpx.Response(429, json={'error': {'code': 'RateLimitExceeded.EndpointTPMExceeded', 'message': 'busy'}})
            return response()
        result = self.sdk(handler)(self.root/'rounds/00', self.body, 'run')
        self.assertTrue(result['decision_complete'])
        self.assertEqual(result['calls'], 3)
        self.assertEqual(result['retry_calls'], 2)
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(len(list(self.root.rglob('issued.json'))), 3)
        self.assertEqual(len(list(self.root.glob('rounds/00/attempts/*/terminal.json'))), 3)

    @patch('genshin_corpus.rag.navi_e2_experiment.time.sleep')
    def test_tpm_attempt_limit_counts_all_failures(self, sleep):
        sdk = self.sdk(lambda r: httpx.Response(429, json={'error': {'code': 'RateLimitExceeded.EndpointTPMExceeded'}}))
        result = sdk(self.root/'rounds/00', self.body, 'run', attempt_limit=2)
        self.assertEqual(result['calls'], 2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['execution_state'], 'NOT_STARTED')

    def test_unknown_disconnect_no_retry_no_resume(self):
        calls = []
        def handler(req):
            calls.append(req)
            raise httpx.ReadTimeout('execution indeterminate', request=req)
        sdk = self.sdk(handler)
        result = sdk(self.root/'rounds/00', self.body, 'run')
        self.assertEqual(result['execution_state'], 'EXECUTION UNKNOWN')
        self.assertEqual(len(calls), 1)
        with self.assertRaises(ExplorationError):
            sdk(self.root/'rounds/00', self.body, 'run')
        self.assertEqual(len(calls), 1)

    def test_ordinary_429_5xx_policy_and_auth_never_retry_or_fallback(self):
        for status in (429, 500, 502, 503, 403, 401):
            result = self.sdk(lambda r: httpx.Response(status, json={'error': {'code': 'content_policy_violation'}}))(
                self.root/f'case{status}/rounds/00', self.body, 'run')
            self.assertEqual(result['calls'], 1)
            self.assertFalse(result['retry_eligible'])

    def test_model_status_usage_and_action_fail_closed(self):
        variants = [dict(model='other-model'), dict(status='in_progress'),
            dict(usage={'total_tokens': 20}), dict(usage={'input_tokens': 12, 'output_tokens': 8, 'total_tokens': 21}),
            dict(usage={'input_tokens': True, 'output_tokens': 8, 'total_tokens': 9}),
            dict(text=''), dict(text='ordinary prose'), dict(event='response.incomplete', status='incomplete'),
            dict(event='response.failed', status='failed')]
        for i, changes in enumerate(variants):
            result = self.sdk(lambda r: response(**changes))(self.root/f'case{i}/rounds/00', self.body, 'run')
            self.assertFalse(result['decision_complete'])
            self.assertEqual(result['calls'], 1)

    def test_action_arguments_are_actually_validated(self):
        for action in ({'action': 'search'}, {'action': 'search', 'query': 'x', 'top_k': True},
            {'action': 'read', 'hit': {'round': -1, 'index': 0}}, {'action': 'clue', 'text': 'x'}, {'action': 'stop'}):
            self.assertFalse(_is_valid_decision_content(json.dumps(action), 'single_action'))

    def test_credential_echo_never_persisted(self):
        result = self.sdk(lambda r: response(text='qwen-secret'))(self.root/'rounds/00', self.body, 'run')
        self.assertFalse(result['complete'])
        for path in self.root.rglob('*'):
            if path.is_file(): self.assertNotIn(b'qwen-secret', path.read_bytes())

    @patch('genshin_corpus.rag.navi_e2_experiment.time.sleep')
    def test_campaign_counts_all_attempts_and_rejects_fourth(self, sleep):
        campaign = initialize_campaign(self.root/'campaign')
        report = run_probe(model='gpt-6-luna', route='tokenmetro',
            output_root=campaign/'gpt-6-luna/probe-01', campaign=campaign, environment=self.env,
            transport=httpx.MockTransport(lambda r: httpx.Response(429,
                json={'error': {'code': 'RateLimitExceeded.EndpointTPMExceeded'}})))
        self.assertEqual(report['requests'], 3)
        with self.assertRaisesRegex(ExplorationError, 'ceiling'):
            campaign_guard(campaign, 'gpt-6-luna')
        self.assertEqual(len(list(campaign.rglob('issued.json'))), 3)

    def test_campaign_stops_unknown_channel_but_other_models_continue(self):
        campaign = initialize_campaign(self.root/'campaign')
        def timeout(req): raise httpx.ReadTimeout('unknown', request=req)
        report = run_probe(model='gpt-6-luna', route='tokenmetro',
            output_root=campaign/'gpt-6-luna/probe-01', campaign=campaign, environment=self.env,
            transport=httpx.MockTransport(timeout))
        self.assertEqual(report['result'], 'BLOCKED')
        with self.assertRaisesRegex(ExplorationError, 'UNKNOWN'):
            campaign_guard(campaign, 'gpt-6-luna')
        campaign_guard(campaign, 'qwen3.8-flash')

    def test_retired_live_sdk_cannot_dispatch_even_explicit_transport(self):
        from genshin_corpus.retrieval.semantic_sdk_runner import _historical_transport_only
        for transport in (None, httpx.HTTPTransport(retries=0)):
            try:
                with self.assertRaisesRegex(ValueError, 'retired'):
                    _historical_transport_only(transport)
            finally:
                if transport: transport.close()
        from genshin_corpus.retrieval.semantic_v2_sdk_benchmark import run_one
        with self.assertRaisesRegex(ValueError, 'retired'):
            run_one(self.root/'never-created', 'glm', 16)
        self.assertFalse((self.root/'never-created').exists())

    def test_terminal_only_text_and_reasoning_summary(self):
        event = {'type':'response.completed','response':{'id':'r','model':'gpt-6-luna','status':'completed',
            'usage':{'input_tokens':12,'output_tokens':8,'total_tokens':20},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(decision())}]},
                      {'type':'reasoning','summary':[{'type':'summary_text','text':'Provider summary.'}]}]}}
        sdk = self.sdk(lambda r: httpx.Response(200,headers={'content-type':'text/event-stream'},
            content='data: '+json.dumps(event)+'\n\n'))
        result = sdk(self.root/'rounds/00', self.body, 'run')
        self.assertTrue(result['decision_complete'])
        self.assertEqual(result['reasoning'], 'Provider summary.')
        self.assertEqual(result['reasoning_event_types'], ['response.reasoning_summary_text.terminal'])

    def test_immutable_provider_config_change_refuses_start(self):
        sdk = self.sdk(lambda r: self.fail('must not send'))
        sdk.config['route']['model_id'] = 'other'
        with self.assertRaisesRegex(ExplorationError, 'configuration changed'):
            sdk(self.root/'rounds/00', self.body, 'run')

    def test_provisional_usage_cannot_replace_missing_terminal_usage(self):
        events = [
            {'type':'response.created','response':{'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}},
            {'type':'response.output_text.delta','delta':json.dumps(decision())},
            {'type':'response.completed','response':{'id':'r','model':'gpt-6-luna','status':'completed'}}]
        wire=''.join('data: '+json.dumps(event)+'\n\n' for event in events)
        result = self.sdk(lambda r:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire))(
            self.root/'rounds/00',self.body,'run')
        self.assertFalse(result['complete'])
        self.assertEqual(result['usage'], 'UNKNOWN')

    def test_terminal_text_disagreement_stops(self):
        events = [
            {'type':'response.output_text.delta','delta':json.dumps(decision())},
            {'type':'response.completed','response':{'id':'r','model':'gpt-6-luna','status':'completed',
                'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2},
                'output':[{'type':'message','content':[{'type':'output_text','text':'different text'}]}]}}]
        wire=''.join('data: '+json.dumps(event)+'\n\n' for event in events)
        result = self.sdk(lambda r:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire))(
            self.root/'rounds/00',self.body,'run')
        self.assertFalse(result['complete'])
        self.assertIn('disagrees',result['error']['message'])

    def frozen_prior(self):
        from hashlib import sha256
        from genshin_corpus.rag.navi_e2_experiment import once
        prior = self.root/'prior'
        initialize_campaign(prior)
        for model in ('gpt-6-luna','glm-5.3-flash','qwen3.8-flash','deepseek-v4.1-flash'):
            stem = prior/model/'probe-01/rounds/00/attempts/01'
            once(stem/'issued.json', {'run_identity':'historical'})
            once(stem/'terminal.json', {'execution_state':'EXECUTION UNKNOWN'})
        rows = [{'path':p.relative_to(prior).as_posix(), 'bytes':p.stat().st_size,
                 'sha256':sha256(p.read_bytes()).hexdigest()} for p in prior.rglob('*') if p.is_file()]
        once(prior/'artifact-manifest.json', {'files':rows})
        return prior

    def test_explicit_followup_counts_prior_unknown_without_resuming_it(self):
        prior = self.frozen_prior()
        frozen = {str(p):p.read_bytes() for p in prior.rglob('*') if p.is_file()}
        campaign = initialize_followup_campaign(self.root/'followup', prior)
        report = run_probe(model='glm-5.3-flash',route='jizhi_glm',
            output_root=campaign/'glm-5.3-flash/probe-01',campaign=campaign,environment=self.env,
            transport=httpx.MockTransport(lambda r: response(model='glm-5.3-flash')))
        self.assertEqual(report['requests'], 1)
        self.assertEqual(report['result'], 'PASS')
        with self.assertRaisesRegex(ExplorationError, 'ceiling'):
            campaign_guard(campaign,'glm-5.3-flash')
        campaign_guard(campaign,'deepseek-v4.1-flash')
        with self.assertRaisesRegex(ExplorationError,'not authorized'):
            campaign_guard(campaign,'gpt-6-luna')
        self.assertEqual(frozen,{str(p):p.read_bytes() for p in prior.rglob('*') if p.is_file()})

    def test_followup_rejects_prior_tampering_and_old_glm_route(self):
        prior = self.frozen_prior()
        campaign = initialize_followup_campaign(self.root/'followup', prior)
        with self.assertRaisesRegex(ExplorationError,'not authorized'):
            run_probe(model='glm-5.3-flash',route='tokenmetro',campaign=campaign,
                output_root=campaign/'glm-5.3-flash/probe-01',environment=self.env,
                transport=httpx.MockTransport(lambda r:self.fail('must not send')))
        (prior/'authorization.json').write_text('{}')
        with self.assertRaisesRegex(ExplorationError,'integrity'):
            campaign_guard(campaign,'deepseek-v4.1-flash')

    def test_glm_uses_only_new_independent_credential(self):
        env = dict(self.env);env.pop('NAVI_GLM_API_KEY')
        with self.assertRaisesRegex(ExplorationError,'NAVI_GLM_API_KEY'):
            E2SDK('jizhi_glm',model='glm-5.3-flash',environment=env,
                transport=httpx.MockTransport(lambda r:self.fail('must not send')))
        with self.assertRaisesRegex(ExplorationError,'matching route'):
            E2SDK('tokenmetro',model='glm-5.3-flash',environment=self.env)


if __name__ == '__main__': unittest.main()
