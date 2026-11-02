"""Shared model-call contracts. All attempts are local, with sockets forbidden."""
from dataclasses import replace
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import httpx

from genshin_corpus import model_calls as m
from genshin_corpus.evidence_io import digest, inventory, read, seal, sha, verify, write
from genshin_corpus.retrieval.navi_offline import cards_v2, metro_chat, requests


class CallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name); self.root = self.base/'job'; self.ledger = self.base/'ledger'
        self.plan = {'fingerprint': digest(['model', 'unchanged body']), 'wire': {'input': 'source'}}
        self.sent = []
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No real network'))
        guard.start(); self.addCleanup(guard.stop)

    def sender(self, states):
        def send(folder, headers):
            self.sent.append((str(folder), headers))
            result = states[min(len(self.sent)-1, len(states)-1)]
            write(folder/'plan.json', self.plan)
            write(folder/'request.json', self.plan['wire'])
            write(folder/'trace.json', {'events': result.get('trace', [])})
            write(folder/'http.json', {'status': result.get('http_status'), 'headers': {'retry-after': '0'}})
            write(folder/'result.json', result); seal(folder)
            return result
        return send

    def good(self):
        return {'state': 'CONTENT_VERIFIED', 'calls': 1, 'http_status': 200,
                'usage': {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5}}

    def unknown(self, code=524):
        return {'state': 'HTTP_REJECTED', 'http_status': code, 'calls': 1, 'usage': 'UNKNOWN'}

    def run_call(self, states, **kwargs):
        return m.run(self.root, self.plan, self.ledger, self.sender(states),
                     channel='route/model', sleep=lambda s: None, **kwargs)

    def test_524_is_uncertain_not_a_permanent_ban_or_zero_tokens(self):
        result = self.run_call([self.unknown()])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(result['accounting']['total_tokens'], 'UNKNOWN')
        self.assertEqual(result['attempt_classifications'][0]['execution'], 'UNKNOWN')
        self.assertFalse(result['automatic_retry'])
        self.assertEqual(self.run_call([self.good()]), result)
        self.assertEqual(len(self.sent), 1)

    def test_authorized_unknown_retry_preserves_both_and_full_total_unknown(self):
        policy = m.Policy(unknown_retries=1, cost_risk_authorization='fixture:one potentially duplicate charged attempt')
        result = self.run_call([self.unknown(), self.good()], policy=policy)
        self.assertEqual(result['state'], 'CONTENT_VERIFIED')
        self.assertEqual(result['calls'], 2)
        self.assertEqual(result['accounting']['known_tokens'], 5)
        self.assertEqual(result['accounting']['total_tokens'], 'UNKNOWN')
        self.assertEqual(result['accounting']['retry_attempts'], 1)
        self.assertEqual(read(self.root/'attempts/0001/result.json')['http_status'], 524)
        verify(self.root)

    def test_unknown_retry_limit_and_429_5xx_do_not_bypass_cost_budget(self):
        for code in (429, 500, 502, 503, 524):
            with self.subTest(code=code):
                self.ledger=self.base/('ledger-'+str(code))
                self.root = self.base/str(code); self.plan = self.plan | {'fingerprint': digest(code)}
                before = len(self.sent)
                result = self.run_call([self.unknown(code)], policy=m.Policy(
                    unknown_retries=1, cost_risk_authorization='one retry'))
                self.assertEqual(len(self.sent)-before, 2)
                self.assertEqual(result['calls'], 2)

    def test_verified_presend_failure_can_retry_without_unknown_budget(self):
        not_sent = {'state': 'UNKNOWN', 'calls': 1, 'usage': 'UNKNOWN',
                    'trace': ['connection.connect_tcp.failed']}
        result = self.run_call([not_sent, self.good()])
        self.assertEqual(result['accounting']['total_tokens'], 5)
        self.assertEqual(result['provider_reported_usage'] if 'provider_reported_usage' in result else result['usage'], self.good()['usage'])
        self.assertEqual(result['accounting']['provider_usage_observations'][0], 'UNKNOWN')

    def test_batch_presend_zero_requires_trace_proof_and_preserves_raw_usage(self):
        batch=m.Batch(self.base/'budget',max_requests=3,max_tokens=20)
        not_sent={'state':'UNKNOWN','calls':1,'usage':'UNKNOWN','trace':['connection.start_tls.failed']}
        self.run_call([not_sent,self.good()],batch=batch,token_allowance=10)
        account=batch.accounting()
        self.assertEqual(account['total_tokens'],5)
        self.assertEqual(account['unknown_usage_requests'],0)
        self.assertEqual(account['proven_not_sent_attempts'],1)
        self.assertIsNone(read(self.base/'budget/requests/000001/outcome.json')['observed_tokens'])
        self.assertEqual(read(self.root/'attempts/0001/result.json')['usage'],'UNKNOWN')
        self.assertEqual(m.Batch(self.base/'budget',max_requests=3,max_tokens=20).accounting()['total_tokens'],5)
        self.assertEqual(m.classify(not_sent,['http11.send_request_headers.started','connection.start_tls.failed'])['execution'],'UNKNOWN')

    def test_json_retry_is_explicit_and_does_not_retry_source_or_schema_errors(self):
        bad = self.good() | {'state': 'CONTENT_INVALID', 'error_codes': ['NON_JSON']}
        policy = m.Policy(format_retries=1, cost_risk_authorization='one new generation for JSON failure')
        result = self.run_call([bad, self.good()], policy=policy)
        self.assertEqual(result['accounting']['total_tokens'], 10)
        self.assertEqual(len(self.sent), 2)
        for code in ('SCHEMA_INVALID', 'SOURCE_OR_CONTENT_CONTRACT_INVALID', 'OUTPUT_TRUNCATED'):
            self.root = self.base/code; self.plan = self.plan | {'fingerprint': digest(code)}
            before = len(self.sent)
            self.run_call([bad | {'error_codes': [code]}], policy=policy)
            self.assertEqual(len(self.sent)-before, 1)

    def test_cost_risk_requires_authorization(self):
        for kwargs in ({'unknown_retries': 1}, {'format_retries': 1}, {'max_attempts': 0}, {'delay_cap': float('nan')}):
            with self.assertRaises(ValueError): m.Policy(**kwargs)

    def test_batch_unknown_is_reserved_and_other_tasks_continue(self):
        batch = m.Batch(self.base/'budget', max_requests=3, max_tokens=30)
        self.run_call([self.unknown()], batch=batch, token_allowance=10)
        self.root = self.base/'other'; self.plan = self.plan | {'fingerprint': digest('other')}
        self.run_call([self.good()], batch=batch, token_allowance=10)
        self.assertEqual(batch.accounting()['request_reservations'], 2)
        self.assertEqual(batch.accounting()['known_tokens'], 5)
        self.assertEqual(batch.accounting()['total_tokens'], 'UNKNOWN')
        self.assertEqual(len(self.sent), 2)

    def test_shared_budget_counts_retry_and_persists_across_instances(self):
        policy = m.Policy(unknown_retries=2, cost_risk_authorization='two maximum')
        batch = m.Batch(self.base/'budget', max_requests=1, max_tokens=10)
        with self.assertRaisesRegex(ValueError, 'BATCH_REQUEST_BUDGET'):
            self.run_call([self.unknown()], batch=batch, token_allowance=10, policy=policy)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(m.Batch(self.base/'budget', max_requests=1,max_tokens=10).accounting()['total_tokens'], 'UNKNOWN')
        with self.assertRaises(ValueError): m.Batch(self.base/'budget', max_requests=2,max_tokens=10)

    def test_channel_circuit_stops_only_same_channel_after_repeated_network_failure(self):
        policy = m.Policy(channel_failure_limit=2)
        self.run_call([self.unknown()], policy=policy)
        self.root = self.base/'second'; self.plan = self.plan | {'fingerprint': digest('second')}
        self.run_call([self.unknown()], policy=policy)
        self.root = self.base/'third'; self.plan = self.plan | {'fingerprint': digest('third')}
        with self.assertRaisesRegex(ValueError, 'CHANNEL_COOLDOWN'):
            self.run_call([self.good()], policy=policy)
        self.assertEqual(len(self.sent), 2)
        result = m.run(self.base/'another-channel',self.plan|{'fingerprint':digest('independent')},self.ledger,
                       self.sender([self.good()]),channel='other',policy=policy)
        self.assertEqual(result['state'], 'CONTENT_VERIFIED')
        with patch.object(m, '_channel_open', return_value=False):
            result = self.run_call([self.good()],policy=policy)
        self.assertEqual(result['state'], 'CONTENT_VERIFIED')

    def test_crash_inside_send_no_resend_without_authorized_risk(self):
        def crash(folder, headers):
            write(folder/'request.json', self.plan['wire']); self.sent.append(1)
            raise RuntimeError('process interrupted after reservation')
        with self.assertRaises(RuntimeError):
            m.run(self.root,self.plan,self.ledger,crash,channel='route/model')
        result = self.run_call([self.good()])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['accounting']['unknown_transmission_attempts'], 1)
        self.assertEqual(result['accounting']['total_tokens'], 'UNKNOWN')

    def test_crash_after_attempt_seal_recovers_without_resend(self):
        original = m._channel_record
        with patch.object(m, '_channel_record', side_effect=RuntimeError('after response persisted')):
            with self.assertRaises(RuntimeError): self.run_call([self.good()])
        result = self.run_call([self.good()])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(result['state'], 'CONTENT_VERIFIED')
        self.assertEqual(result['accounting']['total_tokens'], 5)

    def test_identity_tamper_and_duplicate_root_rejected(self):
        self.run_call([self.good()])
        with self.assertRaises(ValueError): self.run_call([self.good()], policy=m.Policy(max_attempts=4))
        with self.assertRaises(FileExistsError):
            m.run(self.base/'duplicate',self.plan,self.ledger,self.sender([self.good()]),channel='route/model')
        (self.root/'attempts/0001/request.json').write_text('{}')
        with self.assertRaises(ValueError): self.run_call([self.good()])

    def test_legacy_524_recovery_requires_exact_ledger_origin_preserves_original(self):
        origin = self.base/'legacy'; write(origin/'plan.json',self.plan)
        write(origin/'result.json',self.unknown()); seal(origin)
        claim = self.ledger/self.plan['fingerprint']
        write(claim/'reservation.json',{'root':str(origin.resolve())})
        write(claim/'outcome.json',{'state':'UNKNOWN'})
        before = inventory(origin); old_ledger = inventory(claim)
        result=self.run_call([self.good()],legacy_origin=origin)
        self.assertEqual(result['state'],'HTTP_REJECTED');self.assertEqual(len(self.sent),0)
        self.assertEqual(inventory(origin),before)
        self.assertTrue(all(sha(claim/p)==h for p,h in old_ledger.items()))
        self.assertEqual([p.name for p in (claim/'recoveries').iterdir()],['0001'])
        self.assertEqual(read(self.root/'plan.json')['fingerprint'],self.plan['fingerprint'])

    def test_verified_dedup_reuses_same_header_without_unknown_retry_risk(self):
        dedup={'status':'VERIFIED','channel':'route/model','contract_evidence_sha256':digest('fixture contract'),
               'header':'Idempotency-Key'}
        result=self.run_call([self.unknown(),self.good()],deduplication=dedup)
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.sent[0][1],self.sent[1][1])
        self.assertEqual(result['accounting']['total_tokens'],'UNKNOWN')

    def test_retry_after_and_delay_cap(self):
        self.assertEqual(m.delay({'retry-after':'120'},1,m.Policy()),30)
        self.assertEqual(m.delay({'retry-after':'0'},1,m.Policy()),0)
        self.assertEqual(m.delay({'retry-after':'bad'},3,m.Policy()),4)

    def test_partial_legacy_recovery_with_risk_budget_keeps_original_partial_bytes(self):
        origin=self.base/'pending';write(origin/'plan.json',self.plan)
        (origin/'response.partial').write_bytes(b'partial and incomplete')
        claim=self.ledger/self.plan['fingerprint']
        write(claim/'reservation.json',{'root':str(origin.resolve())})
        before=inventory(origin)
        result=self.run_call([self.good()],legacy_origin=origin,
            policy=m.Policy(unknown_retries=1,cost_risk_authorization='offline one uncertain retry'))
        self.assertEqual(result['state'],'CONTENT_VERIFIED')
        self.assertEqual(result['accounting']['total_tokens'],'UNKNOWN')
        self.assertEqual(result['accounting']['request_total'],'UNKNOWN')
        self.assertEqual(inventory(origin),before)
        self.assertEqual(len(self.sent),1)

    def test_successful_resume_reconciles_budget_after_sealed_attempt_crash(self):
        batch=m.Batch(self.base/'budget',max_requests=2,max_tokens=30)
        with patch.object(batch,'finish',side_effect=RuntimeError('crash after response')):
            with self.assertRaises(RuntimeError):self.run_call([self.good()],batch=batch,token_allowance=10)
        self.assertEqual(batch.accounting()['total_tokens'],'UNKNOWN')
        result=self.run_call([self.good()],batch=batch,token_allowance=10)
        self.assertEqual(result['state'],'CONTENT_VERIFIED')
        self.assertEqual(batch.accounting()['total_tokens'],5)
        self.assertEqual(len(self.sent),1)

    def test_interrupted_reservation_before_directory_never_resends_as_fresh(self):
        original=Path.mkdir
        def crash(path,*args,**kwargs):
            if path==self.root/'attempts/0001':raise RuntimeError('crash before attempt dir')
            return original(path,*args,**kwargs)
        with patch.object(Path,'mkdir',crash):
            with self.assertRaises(RuntimeError):self.run_call([self.good()])
        result=self.run_call([self.good()])
        self.assertEqual(result['state'],'UNKNOWN');self.assertEqual(len(self.sent),0)
        self.assertEqual(result['accounting']['request_total'],'UNKNOWN')

    def test_metro_real_adapter_524_then_valid_content_under_offline_authorization(self):
        evidence=[{'id':'e1','text':'她归来。','ru_start':0}]
        view={'title':'fixture','evidence':evidence,'children':[],'mode':'read','card_version':cards_v2.VERSION}
        obj={'id':'chat1','object':'chat.completion','created':1,'model':'deepseek-v4.1-flash',
             'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant',
               'content':json.dumps(cards_v2.mock(view),ensure_ascii=False)}}],
             'usage':{'prompt_tokens':4,'completion_tokens':5,'total_tokens':9}}
        bodies=[]
        class Body(httpx.SyncByteStream):
            def __init__(self,raw):self.raw=raw
            def __iter__(self):yield self.raw
        def handler(req):
            bodies.append(req.content)
            if len(bodies)==1:return httpx.Response(524,stream=Body(b'origin timeout'))
            return httpx.Response(200,headers={'content-type':'application/json'},stream=Body(json.dumps(obj).encode()))
        # Each attempt creates its own client/transport, just as live dispatch would.
        with patch.object(httpx.MockTransport,'close',lambda self:None):
            result=metro_chat.execute(self.root,view,evidence,mock=True,transport_override=httpx.MockTransport(handler),
               call_policy=m.Policy(unknown_retries=1,cost_risk_authorization='fixture only',initial_delay=0))
        self.assertEqual(result['state'],'CONTENT_VERIFIED')
        self.assertEqual(len(bodies),2);self.assertEqual(bodies[0],bodies[1])
        self.assertEqual(result['accounting']['known_tokens'],9)
        self.assertEqual(result['accounting']['total_tokens'],'UNKNOWN')
        self.assertEqual(read(self.root/'value.json')['overview'],cards_v2.mock(view)['overview'])
        verify(self.root)


if __name__=='__main__':unittest.main()
