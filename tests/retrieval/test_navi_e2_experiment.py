"""Small E2 fixtures; no full corpus or provider required."""
import json
from pathlib import Path
from unittest.mock import patch

from tests.retrieval.test_navi_exploration import NaviExplorationTests
from genshin_corpus.rag.navi_e2_experiment import (
    E2Budget, HYPOTHESIS, run_exploration, once, E2SDK, body_chars, model_result, lineage_accounting, short_payload,
)
from genshin_corpus.retrieval.navi_exploration import ExplorationError
from genshin_corpus.canonical.fingerprints import sha256_json


class FakeDecisions:
    def __init__(self, decisions, complete=True, usage=True):
        self.config = {'route':{'model_id':'fake'}, 'adapter':'fake_recorded_not_real'}
        self.decisions, self.complete, self.usage, self.calls = decisions, complete, usage, 0
    def __call__(self, stem, body, identity):
        stem.mkdir(parents=True,exist_ok=True)
        once(stem/'request.json',body)
        once(stem/'issued.json',{'run_identity':identity})
        result = {'calls':1,'complete':self.complete,'content':json.dumps(self.decisions[self.calls]),
                  'usage':{'total_tokens':200} if self.usage else 'UNKNOWN'}
        self.calls += 1
        once(stem/'terminal.json',result)
        return result


def decision(actions, memory='fixture hypothesis'):
    return {'actions':actions,'memory':{'status':HYPOTHESIS,'text':memory},'rationale':'fixture choice'}


class E2Tests(NaviExplorationTests):
    # Reuse E1 checked-in Canonical fixture setup only; inherited E1 tests omitted below.
    def run_e2(self, provider, name='e2', budget=None):
        return run_exploration(self.tools,start_ru=self.uid,output_root=self.root/name,provider=provider,
                               question=self.question,budget=budget or E2Budget())
    def test_autonomous_decisions_and_terminal_resume(self):
        fake=FakeDecisions([decision([{'action':'search','query':'library','top_k':2}]),
                            decision([{'action':'clue','text':'unverified library clue','source_unit_ids':[self.uid]},
                                      {'action':'stop','reason':'enough fixture sources'}])])
        with patch('socket.socket.connect',side_effect=AssertionError('network blocked')):
            s=self.run_e2(fake)
        self.assertEqual(s['status'],'stopped'); self.assertEqual(s['model_rounds'],2)
        self.assertEqual(s['provider_calls'],2); self.assertEqual(s['pending_clues'][0]['status'],HYPOTHESIS)
        with patch.object(self.tools,'read',side_effect=AssertionError('repeat')):
            self.assertEqual(self.run_e2(fake)['checkpoint_identity'],s['checkpoint_identity'])
        self.assertEqual(fake.calls,2)
    def test_changed_decision_after_terminal_rejected(self):
        f=FakeDecisions([decision([{'action':'stop','reason':'done'}])]);self.run_e2(f)
        p=self.root/'e2/rounds/00/decision.json'
        d=json.loads(p.read_text());d['rationale']='changed after provider';p.write_text(json.dumps(d))
        with self.assertRaises(ExplorationError):self.run_e2(f)
        self.assertEqual(f.calls,1)
    def test_unprovided_id_rejected(self):
        f=FakeDecisions([decision([{'action':'read','unit_id':self.other,'max_chars':20000}])])
        s=self.run_e2(f)
        self.assertEqual(s['status'],'partial'); self.assertEqual(s['provider_calls'],1)
        self.assertEqual(s['used']['reads'],1)
    def test_unknown_usage_stops_without_retry(self):
        f=FakeDecisions([decision([{'action':'stop','reason':'stop'}])],usage=False)
        s=self.run_e2(f)
        self.assertEqual(s['status'],'partial'); self.assertEqual(s['provider_audit']['usage_unknown_calls'],1)
        self.run_e2(f); self.assertEqual(f.calls,1)
    def test_partial_provider_preserved(self):
        f=FakeDecisions([decision([{'action':'stop','reason':'stop'}])],complete=False)
        s=self.run_e2(f)
        self.assertEqual(s['status'],'partial'); self.assertEqual(s['provider_calls'],1)
        self.assertTrue((self.root/'e2/rounds/00/terminal.json').exists())
        self.assertEqual(s['used']['tokens'],200)
    def test_fact_memory_rejected(self):
        d=decision([{'action':'stop','reason':'stop'}]); d['memory']['status']='fact'
        s=self.run_e2(FakeDecisions([d]))
        self.assertEqual(s['status'],'partial'); self.assertEqual(s['memory']['status'],HYPOTHESIS)
    def test_hard_round_stop(self):
        f=FakeDecisions([decision([{'action':'clue','text':'line','source_unit_ids':[self.uid]}])])
        s=self.run_e2(f,budget=E2Budget(rounds=1))
        self.assertEqual(s['status'],'round_budget_exhausted'); self.assertEqual(f.calls,1)
    def test_token_preflight_issues_zero(self):
        f=FakeDecisions([])
        s=self.run_e2(f,budget=E2Budget(total_tokens=100))
        self.assertEqual(s['status'],'budget_exhausted'); self.assertEqual(f.calls,0)
    def test_configuration_change_resume_rejected(self):
        f=FakeDecisions([decision([{'action':'stop','reason':'stop'}])]); self.run_e2(f)
        with self.assertRaises(ExplorationError): self.run_e2(f,budget=E2Budget(rounds=5))
    def test_body_count_excludes_metadata(self):
        self.assertEqual(body_chars({'canonical_payload':json.dumps({'units':[{'value':{'text':'abc'},'metadata':'ignored'}]})}),3)
    def test_page_and_character_ceilings(self):
        f=FakeDecisions([])
        s=self.run_e2(f,budget=E2Budget(canonical_chars=1))
        self.assertEqual(s['status'],'budget_exhausted'); self.assertEqual(f.calls,0)
        with self.assertRaises(ExplorationError): E2Budget(rounds=7)
    def test_projection_continuation_preserves_total_budgets(self):
        first=FakeDecisions([decision([{'action':'search','query':'library','top_k':2}])])
        def big(result):
            return {'oversized_search_response':'x'*40000} if 'hits' in result else model_result(result)
        with patch('genshin_corpus.rag.navi_e2_experiment.model_result',side_effect=big):
            parent=self.run_e2(first)
        self.assertEqual(parent['status'],'budget_exhausted');self.assertEqual(parent['model_rounds'],1)
        second=FakeDecisions([decision([{'action':'stop','reason':'bounded continuation'}])])
        child=run_exploration(self.tools,start_ru=self.uid,output_root=self.root/'child',provider=second,
                              question=self.question,parent_root=self.root/'e2')
        self.assertEqual(child['status'],'stopped');self.assertEqual(child['model_rounds'],2)
        self.assertEqual(child['provider_calls'],2);self.assertEqual(child['provider_audit']['issued_calls'],2)
        self.assertEqual(child['used']['reads'],1)
        self.assertEqual(child['used']['searches'],1);self.assertEqual(child['used']['tokens'],400)
        self.assertEqual(json.loads((self.root/'e2/session.json').read_text())['checkpoint_identity'],parent['checkpoint_identity'])
    def test_compact_search_retains_hit_identity_and_truncation(self):
        hit={'unit_id':self.uid,'canonical_address':{'source_identity_key':'fixture','section_ordinal':1,'canonical_unit_ordinal':2,'canonical_unit_kind':'rich_text'},
             'record_context':{'record_title':'Title','section_name':'Section'},'snippet':'x'*200,'snippet_omitted_chars':10}
        r=model_result({'mode':'local','query':'test','hits':[hit]})
        self.assertEqual(r['hits'][0]['unit_id'],self.uid);self.assertEqual(len(r['hits'][0]['snippet']),80)
        self.assertEqual(r['hits'][0]['snippet_omitted_chars'],130)
    def partial_parent(self):
        f=FakeDecisions([decision([{'action':'search','query':'library','top_k':2}]), decision([{'action':'stop','reason':'done'}])])
        class Length(FakeDecisions):
            def __call__(sub,stem,body,identity):
                t=super().__call__(stem,body,identity)
                t['terminal_identity']=sha256_json(t)
                # Fake evidence mimics the already observed terminal/issued/wire contract.
                issued=json.loads((stem/'issued.json').read_text());issued['request_identity']=sha256_json(body)
                (stem/'issued.json').write_text(json.dumps(issued));(stem/'wire-request.json').write_text(json.dumps(body))
                t.update(finish_reason='length' if sub.calls==2 else 'stop',http_status=200,error=None)
                if sub.calls==2:t.update(content='',complete=False)
                t.pop('terminal_identity');t['terminal_identity']=sha256_json(t)
                (stem/'terminal.json').write_text(json.dumps(t))
                return t
        f=Length(f.decisions)
        s=self.run_e2(f)
        self.assertEqual(s['status'],'partial');self.assertEqual(s['model_rounds'],1)
        return s
    def test_explicit_length_continuation_counts_failed_call(self):
        parent=self.partial_parent()
        f=FakeDecisions([{'action':'read','unit_id':self.uid,'max_chars':20000},
                         {'action':'clue','text':'fiction remains attributed','source_unit_ids':[self.uid]},
                         {'action':'stop','reason':'source limit understood'}])
        child=run_exploration(self.tools,start_ru=self.uid,output_root=self.root/'short',provider=f,
            question=self.question,parent_root=self.root/'e2',single_action_continuation=True,budget=E2Budget(output_tokens=4096))
        self.assertEqual(child['status'],'stopped');self.assertEqual(child['provider_calls'],5)
        self.assertEqual(child['model_rounds'],5);self.assertEqual(child['used']['tokens'],1000)
        self.assertEqual(child['used']['searches'],1);self.assertEqual(child['used']['reads'],2)
        self.assertEqual(child['pending_clues'][0]['status'],HYPOTHESIS)
        self.assertEqual(json.loads((self.root/'e2/session.json').read_text())['checkpoint_identity'],parent['checkpoint_identity'])
    def test_continuation_hard_budget_change_rejected(self):
        self.partial_parent()
        with self.assertRaises(ExplorationError):
            run_exploration(self.tools,start_ru=self.uid,output_root=self.root/'short',provider=FakeDecisions([]),
                question=self.question,parent_root=self.root/'e2',single_action_continuation=True,budget=E2Budget(reads=11,output_tokens=4096))
    def test_single_action_batch_rejected_without_retry(self):
        self.partial_parent()
        f=FakeDecisions([decision([{'action':'stop','reason':'illegal wrapper'}])])
        s=run_exploration(self.tools,start_ru=self.uid,output_root=self.root/'short',provider=f,
            question=self.question,parent_root=self.root/'e2',single_action_continuation=True,budget=E2Budget(output_tokens=4096))
        self.assertEqual(s['status'],'partial');self.assertEqual(f.calls,1);self.assertEqual(s['provider_calls'],3)
    def test_short_read_payload_excludes_duplicate_canonical_prefix(self):
        self.partial_parent()
        parent=json.loads((self.root/'e2/session.json').read_text())
        parent['delivered_until']=0
        payload=short_payload(parent,self.question,E2Budget())
        result=payload['new_tool_results'][0]['result']
        self.assertIn('ru_text',result);self.assertNotIn('canonical_units',result)
        self.assertNotIn('canonical_payload',result);self.assertIn('withheld_canonical_payload_chars',result)
        self.assertEqual(result['partial'],parent['read_sources'][0]['partial'])
    def test_single_action_context_stop_child_keeps_accepted_decisions(self):
        self.partial_parent()
        f=FakeDecisions([{'action':'read','unit_id':self.uid,'max_chars':20000}])
        def huge(*args,**kwargs):return {'oversized':'x'*40000}
        with patch('genshin_corpus.rag.navi_e2_experiment.short_payload',side_effect=huge):
            first=run_exploration(self.tools,start_ru=self.uid,output_root=self.root/'blocked',provider=f,
                question=self.question,parent_root=self.root/'e2',single_action_continuation=True,budget=E2Budget(output_tokens=4096))
        self.assertEqual(first['status'],'budget_exhausted');self.assertEqual(f.calls,0)
        stop=FakeDecisions([{'action':'stop','reason':'bounded child'}])
        child=run_exploration(self.tools,start_ru=self.uid,output_root=self.root/'blockedchild',provider=stop,
                question=self.question,parent_root=self.root/'blocked',single_action_continuation=True,budget=E2Budget(output_tokens=4096))
        self.assertEqual(child['status'],'stopped');self.assertEqual(child['accepted_decisions'],2)
        self.assertEqual(child['provider_calls'],3);self.assertEqual(child['used']['tokens'],600)
    def test_unresolved_issued_continuation_stops_and_never_reissues(self):
        self.partial_parent()
        class Interrupted(FakeDecisions):
            def __call__(sub,stem,body,identity):
                stem.mkdir(parents=True,exist_ok=True)
                once(stem/'request.json',body)
                once(stem/'issued.json',{'run_identity':identity})
                sub.calls+=1
                raise ConnectionError('fixture interrupted after issued; terminal UNKNOWN')
        provider=Interrupted([])
        args=dict(start_ru=self.uid,output_root=self.root/'unknown',provider=provider,
            question=self.question,parent_root=self.root/'e2',single_action_continuation=True,
            budget=E2Budget(output_tokens=4096))
        result=run_exploration(self.tools,**args)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['provider_calls'],3)
        self.assertEqual(result['provider_audit']['usage_unknown_calls'],1)
        self.assertEqual(result['model_rounds'],3)
        self.assertEqual(run_exploration(self.tools,**args)['checkpoint_identity'],result['checkpoint_identity'])
        self.assertEqual(provider.calls,1)
    def test_continuation_parent_terminal_tamper_rejected(self):
        self.partial_parent();p=self.root/'e2/rounds/01/terminal.json'
        t=json.loads(p.read_text());t['usage']['total_tokens']=1;p.write_text(json.dumps(t))
        with self.assertRaises(ExplorationError):lineage_accounting(self.root/'e2')
    def test_sdk_429_preserves_failure_without_retry(self):
        import httpx
        transport=httpx.MockTransport(lambda req:httpx.Response(429,json={'error':{'message':'fixture rate limit','code':'RateLimitExceeded.EndpointRPMExceeded'}}))
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'chat_completions'}):
            p=E2SDK(transport=transport)
            t=p(self.root/'sdk429/rounds/00',{'model':'deepseek-v4.1-flash','messages':[{'role':'user','content':'fixture'}],'stream':True,'max_tokens':100},'fixture')
        self.assertFalse(t['complete']);self.assertEqual(t['calls'],1);self.assertEqual(t['http_status'],429)
        self.assertTrue((self.root/'sdk429/rounds/00/raw-error.bin').exists())
    def test_sdk_incomplete_stream_preserved(self):
        import httpx
        wire='data: '+json.dumps({'choices':[{'delta':{'content':'partial'},'finish_reason':None}]})+'\n\n'
        transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire))
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'chat_completions'}):
            p=E2SDK(transport=transport)
            t=p(self.root/'sdkpartial/rounds/00',{'model':'deepseek-v4.1-flash','messages':[{'role':'user','content':'fixture'}],'stream':True,'max_tokens':100},'fixture')
        self.assertFalse(t['complete']);self.assertEqual(t['content'],'partial');self.assertIsNotNone(t['stream'])
    def test_sdk_stream_usage_and_wire_evidence(self):
        import httpx
        chunks=[{'choices':[{'delta':{'content':json.dumps(decision([{'action':'stop','reason':'stop'}]))},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}], 'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}}]
        wire=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire))
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'chat_completions'}):
            provider=E2SDK(transport=transport)
            t=provider(self.root/'sdk/rounds/00',{'model':'deepseek-v4.1-flash','messages':[{'role':'user','content':'fixture'}],'stream':True,'stream_options':{'include_usage':True},'max_tokens':100},'fixture')
        self.assertTrue(t['complete']); self.assertEqual(t['calls'],1); self.assertEqual(t['usage']['total_tokens'],30)
        self.assertNotIn('fixture-secret',(self.root/'sdk/rounds/00/request-metadata.json').read_text())

# Do not rerun inherited E1 cases as if they were new E2 tests.
for name in dir(NaviExplorationTests):
    if name.startswith('test_') and name not in E2Tests.__dict__:
        setattr(E2Tests,name,None)
del NaviExplorationTests
