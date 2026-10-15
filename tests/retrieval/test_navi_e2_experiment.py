"""Small E2 fixtures; no full corpus or provider required."""
from dataclasses import asdict
import json
from pathlib import Path
from unittest.mock import patch

from tests.retrieval.test_navi_exploration import NaviExplorationTests
from genshin_corpus.rag.navi_e2_experiment import (
    E2Budget, HYPOTHESIS, run_exploration, once, E2SDK, body_chars, model_result, lineage_accounting, short_payload,
    SINGLE_ACTION_PROMPT, PROMPT, execute_action,
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
            return {'oversized_search_response':'x'*120000} if 'hits' in result else model_result(result)
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
    def test_new_prompts_use_ru_identity_in_both_modes(self):
        for prompt in (PROMPT, SINGLE_ACTION_PROMPT):
            self.assertIn('unit_id', prompt['tools']['read'])
            self.assertNotIn('hit:', json.dumps(prompt, ensure_ascii=False))
            self.assertIn('model_round', prompt['tools']['read'])

    def test_short_search_retains_formal_ids_for_multiple_candidates(self):
        self.partial_parent()
        state=json.loads((self.root/'e2/session.json').read_text())
        state['delivered_until']=0
        original=next(e for e in state['events'] if e['action']['action']=='search')
        compact=next(e['result'] for e in short_payload(state,self.question,E2Budget())['new_tool_results'] if 'hits' in e['result'])
        self.assertGreaterEqual(len(compact['hits']),2)
        for shown, full in zip(compact['hits'],original['result']['hits']):
            self.assertEqual(shown['unit_id'],full['unit_id'])
            self.assertNotIn('hit',shown)
            self.assertLessEqual(len(shown['snippet']),60)

    def test_batch_round_three_reads_search_actions_five_and_six_by_id(self):
        outer=self
        class FromVisibleIds(FakeDecisions):
            def __call__(sub,stem,body,identity):
                payload=json.loads(body['input'])
                if sub.calls==2:
                    outer.assertEqual(payload['model_round'],3)
                    searches=[e for e in payload['new_tool_results'] if 'hits' in e['result']]
                    outer.assertEqual([e['action_index'] for e in searches],[5,6])
                    sub.decisions.append(decision([
                        {'action':'read','unit_id':e['result']['hits'][0]['unit_id'],'max_chars':20000}
                        for e in searches]+[{'action':'stop','reason':'fixture done'}]))
                return super().__call__(stem,body,identity)
        fake=FromVisibleIds([
            decision([{'action':'search','query':'library','top_k':2},
                      {'action':'search','query':'legend','top_k':2},
                      {'action':'clue','text':'hypothesis','source_unit_ids':[self.uid]}]),
            decision([{'action':'read','unit_id':self.uid,'max_chars':20000},
                      {'action':'search','query':'Library 0','top_k':2},
                      {'action':'search','query':'Library 1','top_k':2}])])
        state=self.run_e2(fake)
        self.assertEqual(state['status'],'stopped')
        self.assertEqual(state['used']['reads'],4)  # seed plus three reads
        self.assertEqual([e['action']['action'] for e in state['events'][5:7]],['search','search'])

    def test_single_action_reads_multiple_visible_ids_and_legacy_coordinates(self):
        self.partial_parent()
        outer=self
        class ReadVisible(FakeDecisions):
            def __call__(sub,stem,body,identity):
                if sub.calls==0:
                    payload=json.loads(body['input'])
                    search=next(e for e in payload['new_tool_results'] if 'hits' in e['result'])
                    hits=search['result']['hits']
                    reference=({'hit':{'round':search['action_index'],'index':1}} if legacy
                               else {'unit_id':hits[1]['unit_id']})
                    sub.decisions=[{'action':'read','unit_id':hits[0]['unit_id'],'max_chars':20000},
                                   {'action':'read',**reference,'max_chars':20000},
                                   {'action':'stop','reason':'done'}]
                    outer.assertNotEqual(search['action_index'],3)  # next model call is 3
                return super().__call__(stem,body,identity)
        for legacy in (False,True):
            with self.subTest(legacy=legacy):
                state=run_exploration(self.tools,start_ru=self.uid,output_root=self.root/f'short-{legacy}',provider=ReadVisible([]),
                    question=self.question,parent_root=self.root/'e2',single_action_continuation=True,budget=E2Budget(output_tokens=4096))
                self.assertEqual(state['status'],'stopped')
                self.assertEqual(state['used']['reads'],3)

    def test_batch_legacy_hit_keeps_global_action_semantics(self):
        fake=FakeDecisions([
            decision([{'action':'clue','text':'hypothesis','source_unit_ids':[self.uid]},
                      {'action':'search','query':'library','top_k':2}]),
            decision([{'action':'read','hit':{'round':2,'index':i},'max_chars':20000} for i in (0,1)]
                     +[{'action':'stop','reason':'done'}])])
        state=self.run_e2(fake)
        self.assertEqual(state['status'],'stopped')
        self.assertEqual(state['used']['reads'],3)

    def test_invalid_references_still_fail_before_read(self):
        self.partial_parent()
        state=json.loads((self.root/'e2/session.json').read_text())
        search=next(e for e in state['events'] if e['action']['action']=='search')
        execute_action(self.tools,state,{'action':'clue','text':'hypothesis','source_unit_ids':[self.uid]},E2Budget())
        bad=[{'hit':{'round':0,'index':0}},  # seed read is not search
             {'hit':{'round':len(state['events'])-1,'index':0}},  # clue is not search
             {'hit':{'round':-1,'index':0}}, {'hit':{'round':True,'index':0}},
             {'hit':{'round':999,'index':0}}, {'hit':{'round':search['round'],'index':999}},
             {'hit':{'action_index':search['round'],'index':0}},
             {'unit_id':'not-provided'}, {'unit_id':''}]
        for reference in bad:
            with self.subTest(reference=reference), patch.object(self.tools,'read',side_effect=AssertionError('invalid read dispatched')):
                with self.assertRaises(ExplorationError):
                    execute_action(self.tools,state,{'action':'read',**reference},E2Budget())
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
        def huge(*args,**kwargs):return {'oversized':'x'*120000}
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
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'responses'}):
            p=E2SDK(route='tokenmetro', model='gpt-6-luna', transport=transport)
            t=p(self.root/'sdk429/rounds/00',{'model':'gpt-6-luna','instructions':'inst','input':'fixture','stream':True,'max_output_tokens':100},'fixture')
        self.assertFalse(t['complete']);self.assertEqual(t['calls'],1);self.assertEqual(t['http_status'],429)
        self.assertTrue((self.root/'sdk429/rounds/00/attempts/01/raw-error.bin').exists())
    def test_sdk_incomplete_stream_preserved(self):
        import httpx
        wire='data: '+json.dumps({'type':'response.output_text.delta','delta':'partial'})+'\n\n'
        transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire))
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'responses'}):
            p=E2SDK(route='tokenmetro', model='gpt-6-luna', transport=transport)
            t=p(self.root/'sdkpartial/rounds/00',{'model':'gpt-6-luna','instructions':'inst','input':'fixture','stream':True,'max_output_tokens':100},'fixture')
        self.assertFalse(t['complete']);self.assertEqual(t['content'],'partial');self.assertIsNotNone(t['stream'])
    def test_sdk_stream_usage_and_wire_evidence(self):
        import httpx
        chunks=[{'type':'response.reasoning_text.delta','delta':'thinking'},
                {'type':'response.output_text.delta','delta':json.dumps(decision([{'action':'stop','reason':'stop'}]))},
                {'type':'response.completed','response':{'model':'gpt-6-luna','status':'completed','id':'resp_1','usage':{'input_tokens':20,'output_tokens':10,'total_tokens':30}}}]
        wire=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire))
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'responses'}):
            provider=E2SDK(route='tokenmetro', model='gpt-6-luna', transport=transport)
            t=provider(self.root/'sdk/rounds/00',{'model':'gpt-6-luna','instructions':'inst','input':'fixture','stream':True,'max_output_tokens':100},'fixture')
        self.assertTrue(t['complete']); self.assertEqual(t['calls'],1); self.assertEqual(t['usage']['total_tokens'],30)
        self.assertEqual(t['reasoning'],'thinking')
        meta = json.loads((self.root/'sdk/rounds/00/attempts/01/request-metadata.json').read_text())
        self.assertTrue(meta['url'].endswith('/responses'))
        self.assertNotIn('fixture-secret',(self.root/'sdk/rounds/00/attempts/01/request-metadata.json').read_text())
    def test_sdk_in_stream_error_event_captured(self):
        import httpx
        chunks=[{'type':'error','error':{'message':'provider internal rate error','code':'rate_limit'}}]
        wire=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire))
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'responses'}):
            provider=E2SDK(route='tokenmetro', model='gpt-6-luna', transport=transport)
            t=provider(self.root/'sdkerr/rounds/00',{'model':'gpt-6-luna','instructions':'inst','input':'fixture','stream':True,'max_output_tokens':100},'fixture')
        self.assertFalse(t['complete'])
        self.assertIn(t['error']['type'], ('ResponsesStreamError', 'APIError'))
        self.assertIn('provider internal rate error',t['error']['message'])
    def test_sdk_response_failed_event_captured(self):
        import httpx
        chunks=[{'type':'response.failed','response':{'model':'gpt-6-luna','status':'completed','id':'resp_fail','error':{'message':'safety check reject'}}}]
        wire=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire))
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'responses'}):
            provider=E2SDK(route='tokenmetro', model='gpt-6-luna', transport=transport)
            t=provider(self.root/'sdkfailed/rounds/00',{'model':'gpt-6-luna','instructions':'inst','input':'fixture','stream':True,'max_output_tokens':100},'fixture')
        self.assertFalse(t['complete'])
        self.assertEqual(t['error']['type'],'ResponsesFailedError')
        self.assertIn('safety check reject',t['error']['message'])
    def test_sdk_rejects_chat_completions_surface(self):
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'chat_completions'}):
            with self.assertRaises(ExplorationError) as ctx:
                E2SDK(route='tokenmetro', model='gpt-6-luna', )
            self.assertIn('Responses API surface',str(ctx.exception))
    def test_sdk_rejects_unsupported_route(self):
        with patch.dict('os.environ',{'JIZHI_API_KEY':'fixture-secret','JIZHI_BASE_URL':'https://example.invalid/v1'}):
            with self.assertRaises(ExplorationError) as ctx:
                E2SDK(route='jizhi', model='gpt-6-luna')
            self.assertIn('matching route',str(ctx.exception))
    def test_sdk_empty_visible_content_fails(self):
        import httpx
        chunks=[{'type':'response.reasoning_text.delta','delta':'only thought, no visible output'},
                {'type':'response.completed','response':{'model':'gpt-6-luna','status':'completed','id':'resp_empty','usage':{'input_tokens':20,'output_tokens':10,'total_tokens':30}}}]
        wire=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire))
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'responses'}):
            provider=E2SDK(route='tokenmetro', model='gpt-6-luna', transport=transport)
            t=provider(self.root/'sdkempty/rounds/00',{'model':'gpt-6-luna','instructions':'inst','input':'fixture','stream':True,'max_output_tokens':100},'fixture')
        self.assertFalse(t['complete'])
        self.assertEqual(t['content'],'')
        self.assertEqual(t['reasoning'],'only thought, no visible output')
    def test_sdk_missing_usage_fails_complete(self):
        import httpx
        chunks=[{'type':'response.output_text.delta','delta':json.dumps(decision([{'action':'stop','reason':'stop'}]))},
                {'type':'response.completed','response':{'model':'gpt-6-luna','status':'completed','id':'resp_no_usage'}}]
        wire=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire))
        with patch.dict('os.environ',{'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'responses'}):
            provider=E2SDK(route='tokenmetro', model='gpt-6-luna', transport=transport)
            t=provider(self.root/'sdknousage/rounds/00',{'model':'gpt-6-luna','instructions':'inst','input':'fixture','stream':True,'max_output_tokens':100},'fixture')
        self.assertFalse(t['complete'])
        self.assertTrue(bool(t['content']))
    def test_exploration_invalid_action_json_fails(self):
        class BadJsonProvider:
            def __init__(self):
                self.config={'route':{'model_id':'gpt-6-luna'}}
            def __call__(self, stem, body, identity):
                return {'calls': 1, 'http_status': 200, 'headers': {}, 'run_identity': identity,
                        'finish_reason': 'stop', 'complete': True, 'content': 'not valid json',
                        'usage': {'total_tokens': 100}, 'usage_normalized': {}}
        state = run_exploration(self.tools, start_ru=self.uid, output_root=self.root/'bad_json_run',
                                provider=BadJsonProvider(), question=self.question)
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['error']['type'], 'ExplorationError')
        self.assertIn('invalid action JSON', state['error']['message'])
    def test_budget_hard_ceiling_assertions(self):
        b=E2Budget(total_tokens=100000,output_tokens=8192)
        self.assertEqual(b.total_tokens,100000)
        self.assertEqual(b.output_tokens,8192)
        with self.assertRaises(ExplorationError):
            E2Budget(total_tokens=100001)
        with self.assertRaises(ExplorationError):
            E2Budget(output_tokens=8193)
    def test_continuation_rejects_upgrading_legacy_budget(self):
        parent_dir=self.root/'legacy_parent'
        parent_dir.mkdir(parents=True)
        legacy_budget=E2Budget(total_tokens=30000,output_tokens=2048)
        config={'runner':'p05-navi-e2-0.1','question':self.question,'start_ru':self.uid,
                'tools_identity':self.tools.identity,'ru_build_identity':self.tools.ru_identity,
                'inputs':{k:str(getattr(self.tools,k)) for k in ('canonical_path','ru_path','lexical_path')},
                'prompt':SINGLE_ACTION_PROMPT,'single_action_continuation':True,
                'budget':asdict(legacy_budget),'provider':{'route':{'model_id':'fake'},'adapter':'fake'}}
        parent_identity=sha256_json(config)
        parent_state={
            'schema_version':'p05-navi-e2-session-0.1','run_identity':parent_identity,
            'binding':{'question':self.question,'ru_build_identity':self.tools.ru_identity,'tools_identity':self.tools.identity},
            'status':'budget_exhausted','termination':'token dispatch reservation ceiling',
            'events':[],'available_ids':[self.uid],'read_sources':[],'pages':[],'pending_clues':[],
            'memory':{'status':HYPOTHESIS,'text':''},'model_rounds':1,'provider_calls':1,'pending_round':None,
            'semantic_acceptance_status':'not_accepted',
            'used':{'reads':0,'searches':0,'canonical_chars':0,'tool_response_chars':0,'tokens':200,'token_reservations':200},
            'budget':asdict(legacy_budget),
        }
        parent_state['checkpoint_identity']=sha256_json(parent_state)
        (parent_dir/'session.json').write_text(json.dumps(parent_state),encoding='utf-8')
        (parent_dir/'config.json').write_text(json.dumps(config),encoding='utf-8')
        with self.assertRaises(ExplorationError) as ctx:
            run_exploration(self.tools,start_ru=self.uid,output_root=self.root/'child_upgraded',
                            provider=FakeDecisions([]),question=self.question,parent_root=parent_dir,
                            single_action_continuation=True,budget=E2Budget(output_tokens=4096))
        self.assertIn('parent hard budgets cannot reset/change',str(ctx.exception))

    def test_sdk_decision_complete_distinguishes_valid_vs_invalid_json(self):
        import httpx
        valid_chunk = [{'type':'response.output_text.delta','delta':json.dumps(decision([{'action':'stop','reason':'stop'}]))},
                       {'type':'response.completed','response':{'model':'gpt-6-luna','status':'completed','id':'resp_valid','usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}}}]
        invalid_chunk = [{'type':'response.output_text.delta','delta':'this is definitely not valid json'},
                         {'type':'response.completed','response':{'model':'gpt-6-luna','status':'completed','id':'resp_invalid','usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}}}]
        wire_valid = ''.join('data: '+json.dumps(c)+'\n\n' for c in valid_chunk)+'data: [DONE]\n\n'
        wire_invalid = ''.join('data: '+json.dumps(c)+'\n\n' for c in invalid_chunk)+'data: [DONE]\n\n'
        env = {'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'responses'}
        p_valid = E2SDK(route='tokenmetro', model='gpt-6-luna', transport=httpx.MockTransport(lambda req: httpx.Response(200, headers={'content-type':'text/event-stream'}, content=wire_valid)), environment=env)
        t_valid = p_valid(self.root/'sdk_valid/rounds/00', {'model':'gpt-6-luna','instructions':'i','input':'u','stream':True,'max_output_tokens':100}, 'id1')
        self.assertTrue(t_valid['complete'])
        self.assertTrue(t_valid['decision_complete'])
        p_invalid = E2SDK(route='tokenmetro', model='gpt-6-luna', transport=httpx.MockTransport(lambda req: httpx.Response(200, headers={'content-type':'text/event-stream'}, content=wire_invalid)), environment=env)
        t_invalid = p_invalid(self.root/'sdk_invalid/rounds/00', {'model':'gpt-6-luna','instructions':'i','input':'u','stream':True,'max_output_tokens':100}, 'id2')
        self.assertTrue(t_invalid['complete'])
        self.assertFalse(t_invalid['decision_complete'])

    def test_sdk_decision_complete_mode_mismatch_fails_decision_complete(self):
        import httpx
        env = {'TOKENMETRO_API_KEY':'fixture-secret','TOKENMETRO_BASE_URL':'https://example.invalid/v1','TOKENMETRO_API_SURFACE':'responses'}
        single_action_wire = ''.join('data: '+json.dumps(c)+'\n\n' for c in [
            {'type':'response.output_text.delta','delta':json.dumps({'action':'search','query':'test','top_k':3})},
            {'type':'response.completed','response':{'model':'gpt-6-luna','status':'completed','id':'resp_single','usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}}},
        ]) + 'data: [DONE]\n\n'
        batch_wire = ''.join('data: '+json.dumps(c)+'\n\n' for c in [
            {'type':'response.output_text.delta','delta':json.dumps(decision([{'action':'search','query':'test','top_k':3}]))},
            {'type':'response.completed','response':{'model':'gpt-6-luna','status':'completed','id':'resp_batch','usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}}},
        ]) + 'data: [DONE]\n\n'

        p1 = E2SDK(route='tokenmetro', model='gpt-6-luna', transport=httpx.MockTransport(lambda req: httpx.Response(200, headers={'content-type':'text/event-stream'}, content=single_action_wire)), environment=env)
        t1 = p1(self.root/'sdk_mismatch_1/rounds/00', {'model':'gpt-6-luna','instructions':'i','input':'u','stream':True,'max_output_tokens':100}, 'id1', decision_mode='batch')
        self.assertTrue(t1['complete'])
        self.assertFalse(t1['decision_complete'])

        p2 = E2SDK(route='tokenmetro', model='gpt-6-luna', transport=httpx.MockTransport(lambda req: httpx.Response(200, headers={'content-type':'text/event-stream'}, content=batch_wire)), environment=env)
        t2 = p2(self.root/'sdk_mismatch_2/rounds/00', {'model':'gpt-6-luna','instructions':'i','input':'u','stream':True,'max_output_tokens':100}, 'id2', decision_mode='single_action')
        self.assertTrue(t2['complete'])
        self.assertFalse(t2['decision_complete'])

        p3 = E2SDK(route='tokenmetro', model='gpt-6-luna', transport=httpx.MockTransport(lambda req: httpx.Response(200, headers={'content-type':'text/event-stream'}, content=single_action_wire)), environment=env)
        t3 = p3(self.root/'sdk_mismatch_3/rounds/00', {'model':'gpt-6-luna','instructions':'i','input':'u','stream':True,'max_output_tokens':100}, 'id3', decision_mode='single_action')
        self.assertTrue(t3['complete'])
        self.assertTrue(t3['decision_complete'])

    def test_run_exploration_rejects_schema_mismatch_without_incrementing_accepted_decisions(self):
        class SingleActionFakeProvider:
            config = {'route': {'model_id': 'gpt-6-luna'}}
            def __call__(self, stem, body, run_identity, *, decision_mode=None):
                stem.mkdir(parents=True, exist_ok=True)
                text = json.dumps({'action': 'search', 'query': 'q', 'top_k': 3})
                t = {
                    'run_identity': run_identity, 'calls': 1, 'complete': True,
                    'stream_complete': True, 'decision_complete': False,
                    'content': text, 'reasoning': '', 'finish_reason': 'stop',
                    'usage': {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20},
                    'usage_normalized': {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20},
                    'error': None, 'latency_ms': 10.0, 'stream': {}, 'actual_cost': 'UNKNOWN', 'currency': 'UNKNOWN',
                }
                t['terminal_identity'] = sha256_json(t)
                once(stem / 'terminal.json', t)
                return t

        out = self.root / 'schema_mismatch_run'
        state = run_exploration(self.tools, start_ru=self.uid, output_root=out,
                                provider=SingleActionFakeProvider(), question=self.question)
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['error']['type'], 'ExplorationError')
        self.assertIn('decision does not conform to exploration mode schema', state['error']['message'])
        self.assertEqual(state.get('accepted_decisions', 0), 0)
        self.assertFalse((out / 'rounds' / '00' / 'decision.json').exists())

    def test_type_error_after_dispatch_does_not_invoke_provider_twice(self):
        class RaisesTypeError(FakeDecisions):
            def __call__(sub, stem, body, identity, *, decision_mode=None):
                sub.calls += 1
                once(stem/'issued.json', {'run_identity': identity})
                raise TypeError('fixture error after issuance')
        provider = RaisesTypeError([])
        state = self.run_e2(provider)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['provider_audit']['usage_unknown_calls'], 1)

    @patch('genshin_corpus.rag.navi_e2_experiment.time.sleep')
    def test_sdk_retries_count_against_research_budget_and_offline_recovery(self, sleep):
        import httpx
        from tests.retrieval.test_navi_e2_responses import response
        issued = []
        def handler(req):
            issued.append(req)
            if len(issued) == 1:
                return httpx.Response(429, json={'error': {'code': 'RateLimitExceeded.EndpointTPMExceeded'}})
            return response(text=json.dumps(decision([{'action':'stop','reason':'fixture complete'}])))
        provider = E2SDK('tokenmetro', model='gpt-6-luna', transport=httpx.MockTransport(handler),
            environment={'TOKENMETRO_API_KEY':'fixture-secret', 'TOKENMETRO_BASE_URL':'https://example.invalid/v1'})
        state = self.run_e2(provider)
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(state['provider_calls'], 2)
        self.assertEqual(state['model_rounds'], 2)
        self.assertEqual(state['provider_audit']['retry_calls'], 1)
        self.assertEqual(state['provider_audit']['usage_unknown_calls'], 0)
        self.assertEqual(state['used']['tokens'], 20)
        self.assertEqual(lineage_accounting(self.root/'e2')['calls'], 2)
        self.assertEqual(self.run_e2(provider)['checkpoint_identity'], state['checkpoint_identity'])
        self.assertEqual(len(issued), 2)
        path = self.root/'e2/rounds/00/attempts/02/terminal.json'
        value = json.loads(path.read_text('utf-8')); value['content'] = 'tampered'
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ExplorationError, 'integrity'):
            self.run_e2(provider)

# Do not rerun inherited E1 cases as if they were new E2 tests.
for name in dir(NaviExplorationTests):
    if name.startswith('test_') and name not in E2Tests.__dict__:
        setattr(E2Tests,name,None)
del NaviExplorationTests
