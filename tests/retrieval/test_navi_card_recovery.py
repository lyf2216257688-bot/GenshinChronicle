"""Regression from the live 32215-output and synthesis E079/E080 failures."""
from copy import deepcopy
from pathlib import Path
import json, socket, tempfile, unittest
from unittest.mock import patch
from jsonschema import ValidationError
from genshin_corpus.retrieval.navi_offline import card_generation as g, card_contract as contract
from genshin_corpus.retrieval.navi_offline import card_build as build, cards_v2 as v2, anchors, card_recovery
from genshin_corpus.retrieval.navi_offline.common import read, sha, digest
from tests.retrieval.test_navi_link_delivery import LinkDeliveryTests

class BoundedRecoveryTests(unittest.TestCase):
    sources=LinkDeliveryTests.sources
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'));p.start();self.addCleanup(p.stop)
    def view(self):
        qs=[{'id':str(i),'source_id':'s','canonical_sha256':'c','unit_id':str(i),'ru_start':0,'ru_end':len(t),'text':t}
            for i,t in enumerate(['甲出发。乙留下。','甲出发。丙等待。'])]
        return {'mode':'read','title':'fixture','children':[],'evidence':qs},qs
    def response(self,v,output=1,input=1):
        return {'id':'raw','object':'chat.completion','model':'deepseek-v4.1-flash-1017',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(v)}}],
            'usage':{'prompt_tokens':input,'completion_tokens':output,'total_tokens':input+output}}
    def test_real_output_usage_is_output_not_total_and_stays_rejected(self):
        view,qs=self.view();value=g.mock(view,g.EVIDENCE_32K)
        obj=self.response(value,32215,14065);r,_=g.assess(json.dumps(obj),obj,view,qs,g.EVIDENCE_32K)
        self.assertEqual(r['usage'],{'input_tokens':14065,'output_tokens':32215,'total_tokens':46280})
        self.assertEqual(r['content_validation']['status'],'PASS');self.assertIn('OUTPUT_LIMIT_EXCEEDED',r['error_codes'])
        obj=self.response(g.mock(view,g.BOUNDED),32215,14065)
        r,_=g.assess(json.dumps(obj),obj,view,qs,g.BOUNDED)
        self.assertEqual(r['card_failure'],'OUTPUT_LIMIT_EXCEEDED');self.assertEqual(r['protocol_validation']['status'],'FAIL')
    def test_synthesis_bad_alias_is_not_a_segment_error_and_never_guessed(self):
        view,qs=self.view();child={'card_id':'child','level':'reading_batch','section':None,
            **g.validate_value(g.mock(view,g.EVIDENCE_32K),view,qs,g.EVIDENCE_32K)}
        synth=v2.synthesis_view('fixture',[child],{},preserve_locations=True)
        old=g.mock(synth,g.EVIDENCE_32K)
        old['hypotheses']=[{'text':'未证假说','evidence_ids':['E001','E079','E080']}]
        with self.assertRaisesRegex(ValueError,'Unknown Card wire source alias'):g.validate_value(old,synth,qs,g.EVIDENCE_32K)
        new=g.mock(synth,g.BOUNDED);new['hypotheses']=[{'text':'未证假说','anchors':[{'anchor_id':'A001'}]}]
        accepted=g.validate_value(new,synth,qs,g.BOUNDED)
        self.assertEqual(accepted['hypotheses'][0]['evidence_ids'],['0'])
        new['hypotheses'][0]['anchors'][0]['anchor_id']='E079'
        with self.assertRaises((ValidationError,ValueError)):g.validate_value(new,synth,qs,g.BOUNDED)
    def test_program_derives_every_source_without_cross_evidence_or_fuzzy_match(self):
        view,qs=self.view();value=g.mock(view,g.BOUNDED)
        value['hypotheses']=[{'text':'待核','anchors':[{'start_segment_id':'S0003','end_segment_id':'S0004'}]}]
        self.assertEqual(g.validate_value(value,view,qs,g.BOUNDED)['hypotheses'][0]['evidence_ids'],['1'])
        for a in [{'start_segment_id':'S0001','end_segment_id':'S0003'},
                  {'start_segment_id':'S001','end_segment_id':'S0002'},
                  {'start_segment_id':'S0002','end_segment_id':'S0001'}]:
            bad=deepcopy(value);bad['hypotheses'][0]['anchors']=[a]
            with self.assertRaises((ValueError,ValidationError)):g.validate_value(bad,view,qs,g.BOUNDED)
        bad=deepcopy(value);bad['hypotheses'][0]['evidence_ids']=['E001']
        with self.assertRaises(ValidationError):g.validate_value(bad,view,qs,g.BOUNDED)
    def test_long_source_partitions_and_completed_dependencies_replay(self):
        sources=self.sources([('剧情对话',[('rich_text','甲在场。乙离开。'*1100)])])
        run=self.root/'run';build.prepare_snapshot(run,sources.root,total_request_limit=100,generation_strategy=g.BOUNDED)
        seen=[];normal=build.mock_transport
        def observe(view,strategy):
            seen.append(view);return normal(view,strategy)
        with patch.object(build,'mock_transport',side_effect=observe):
            result=build.run(run,max_new_requests=100)
        self.assertEqual(result['sources'][0]['status'],'COMPLETE')
        self.assertTrue(all(sum(len(q['text']) for q in v['evidence'])<=4000 for v in seen if v['mode']=='read'))
        self.assertEqual(build.audit(run)['document_integrity'],'PASS')
        self.assertEqual(build.run(run)['new_sdk_calls'],0)
        folder=run/result['sources'][0]['directory']
        self.assertEqual(card_recovery.validate_dependencies(run,folder,next(iter(sources.documents)),sources)['status'],'PASS')
    def test_verified_old_source_reuse_has_zero_new_calls_and_keeps_wire(self):
        sources=self.sources([('故事',[('rich_text','甲出发。乙留下。')])])
        old=self.root/'old';build.prepare_snapshot(old,sources.root,total_request_limit=100,generation_strategy=g.EVIDENCE_32K)
        build.run(old,max_new_requests=100);identity=sha(next((old/'jobs').glob('*/complete.json')))
        new=self.root/'new';build.prepare_snapshot(new,sources.root,total_request_limit=100,generation_strategy=g.BOUNDED)
        result=build.run(new,max_new_requests=100,reuse=[old])
        self.assertEqual(result['new_sdk_calls'],0);self.assertEqual(result['sources'][0]['status'],'COMPLETE')
        self.assertEqual(sha(next((new/'jobs').glob('*/complete.json'))),identity)
        self.assertEqual(build.audit(new)['document_integrity'],'PASS')
    def test_schema_has_finite_arrays_strings_and_exact_choice_lists(self):
        view,qs=self.view();schema=contract.schema(view,'read');value=g.mock(view,g.BOUNDED)
        for changed in ('too_many','too_long','missing_receipt'):
            bad=deepcopy(value)
            if changed=='too_many':bad['source_observations']*=21
            elif changed=='too_long':bad['overview']='字'*1801
            else:bad['read_ids']=[]
            with self.assertRaises(ValidationError):g.validate_value(bad,view,qs,g.BOUNDED)
    def test_claimed_coverage_cannot_replace_reachable_successful_reading(self):
        sources=self.sources([('故事',[('rich_text','甲出发。乙留下。')])])
        run=self.root/'run';build.prepare_snapshot(run,sources.root,total_request_limit=100,generation_strategy=g.BOUNDED)
        result=build.run(run,max_new_requests=100);folder=run/result['sources'][0]['directory']
        saved=read(folder/'cards.json');broken=deepcopy(saved)
        for c in broken['cards']+broken['reading_batches']+broken['synthesis_nodes']:c['parts']=[]
        original=card_recovery.read
        with patch.object(card_recovery,'read',side_effect=lambda p:broken if Path(p)==folder/'cards.json' else original(p)):
            with self.assertRaisesRegex(ValueError,'reachable successful direct read'):
                card_recovery.validate_dependencies(run,folder,next(iter(sources.documents)),sources)
    def test_observed_module_group_packing_retains_every_range_and_legacy_plan(self):
        from genshin_corpus.retrieval.navi_offline import card_structure as st
        sources=self.sources([('任务概述',[('rich_text','甲出发。')]),('剧情对话',[('rich_text','乙留下。')])])
        sid=next(iter(sources.documents));record=sources.record(sid)
        record['sections'][0]['source_metadata']['source_module_id']='a'
        record['sections'][1]['source_metadata']['source_module_id']='b'
        record['record_metadata']['source_template_layout']={'tab':[{'module_group':[{'module':[{'id':'a'},{'id':'b'}]}]}]}
        legacy=st.plan(sources,sid);new=st.plan(sources,sid,group_modules=True)
        self.assertEqual(legacy['section_groups'],{0:0,1:1});self.assertEqual(new['section_groups'],{0:0,1:0})
        self.assertEqual(new['selected'],legacy['selected']);self.assertEqual(new['units'],legacy['units'])
        self.assertEqual(new['version'],st.BOUNDED_POLICY)
    def test_imported_successes_do_not_consume_new_dispatch_budget(self):
        sources=self.sources([('故事',[('rich_text','甲出发。乙留下。')])])
        old=self.root/'old';build.prepare_snapshot(old,sources.root,source_ids=[next(iter(sources.documents))],
            total_request_limit=10,generation_strategy=g.EVIDENCE_32K)
        build.run(old,max_new_requests=10)
        new=self.root/'new';build.prepare_snapshot(new,sources.root,total_request_limit=1,generation_strategy=g.BOUNDED)
        result=build.run(new,max_new_requests=10,reuse=[old])
        self.assertTrue(all(s['status']=='COMPLETE' for s in result['sources']))
        self.assertEqual(result['new_sdk_calls'],1)
        self.assertEqual(result['call_accounting']['request_reservations'],1)
    def test_revision_reuses_successful_reorganized_scope_without_rereading_it(self):
        from tests.retrieval.test_navi_card_32k import transport
        sources=self.sources([('剧情对话',[('rich_text','甲出发。乙留下。'*100)])])
        sid=next(iter(sources.documents));old=self.root/'old'
        build.prepare_snapshot(old,sources.root,source_ids=[sid],total_request_limit=100,
            body_limit=200,generation_strategy=g.BOUNDED)
        normal=build.mock_transport;seen=[]
        def first_bad(view,strategy):
            seen.append(view)
            if len(seen)==1:
                value={'id':'failed','object':'chat.completion','created':1,'model':g.wire(view,strategy)['model'],
                    'choices':[{'index':0,'finish_reason':'length','message':{'role':'assistant','content':'{"overview":'}}],
                    'usage':{'prompt_tokens':1,'completion_tokens':2,'total_tokens':3}}
                return transport(value)
            return normal(view,strategy)
        with patch.object(build,'mock_transport',side_effect=first_bad):result=build.run(old,max_new_requests=4)
        self.assertEqual(result['sources'][0]['status'],'PARTIAL_REQUIRES_TARGETED_ACTION')
        recovered=read(old/result['sources'][0]['directory']/'cards.json')['reading_batches'][0]
        spans=recovered['coverage']['supported_ru_ranges'];first_uid=next(iter(spans));first_end=max(e for _,e in spans[first_uid])
        new=self.root/'new';build.prepare_snapshot(new,sources.root,source_ids=[sid],total_request_limit=100,
            body_limit=200,generation_strategy=g.SINGLE,revision='quota-continuation',reason='Preserve completed reading subtree')
        new_views=[]
        def observe(view,strategy):new_views.append(view);return normal(view,strategy)
        with patch.object(build,'mock_transport',side_effect=observe):final=build.run(new,max_new_requests=100,reuse=[old])
        self.assertEqual(final['sources'][0]['status'],'COMPLETE',read(new/final['sources'][0]['directory']/'cards.json')['failures'])
        for view in new_views:
            if view['mode']=='read':
                for q in view['evidence']:
                    self.assertFalse(q['id'] in {x['id'] for x in seen[0]['evidence']},'Recovered original scope was needlessly reread')
        self.assertTrue(list((new/'reuse-origins/scopes').glob('*/origin.json')))
        self.assertEqual(build.audit(new)['document_integrity'],'PASS')
        saved=read(new/final['sources'][0]['directory']/'cards.json')
        reused_card=next(c for c in saved['reading_batches'] if c['card_id']==recovered['card_id'])
        self.assertEqual(reused_card['quote_contract'],contract.CONTRACT)
    def test_identical_text_at_distinct_occurrences_has_distinct_wire_identity(self):
        from genshin_corpus.retrieval.navi_offline import metro_chat
        view,qs=self.view();view['evidence']=[qs[0]]
        another=deepcopy(view);another['evidence'][0].update(id='different-exact-occurrence',ru_start=10,ru_end=18)
        a=g.wire(view,g.BOUNDED);b=g.wire(another,g.BOUNDED)
        self.assertNotEqual(metro_chat.fingerprint(a),metro_chat.fingerprint(b))
        self.assertEqual(metro_chat.fingerprint(a),metro_chat.fingerprint(g.wire(view,g.BOUNDED)))
        self.assertEqual(g.model_view(view,g.BOUNDED)['evidence'][0]['segments'],g.model_view(another,g.BOUNDED)['evidence'][0]['segments'])
    def test_single_segment_contract_derives_separate_exact_ranges_and_rejects_guessing(self):
        view,qs=self.view();value=g.mock(view,g.SINGLE)
        value['source_observations'][0]['anchors']=[{'segment_id':'S0001'},{'segment_id':'S0003'}]
        before=deepcopy(value);accepted=g.validate_value(value,view,qs,g.SINGLE)
        self.assertEqual(value,before)
        self.assertEqual(accepted['source_observations'][0]['evidence_ids'],['0','1'])
        self.assertEqual([a['quote'] for a in accepted['source_observations'][0]['anchors']],['甲出发。','甲出发。'])
        self.assertEqual([a['evidence_id'] for a in accepted['source_observations'][0]['anchors']],['0','1'])
        for bad in [{'segment_id':'E001'},{'segment_id':'S9999'},
                    {'start_segment_id':'S0001','end_segment_id':'S0003'}]:
            invalid=deepcopy(value);invalid['source_observations'][0]['anchors']=[bad]
            with self.assertRaises((ValueError,ValidationError)):g.validate_value(invalid,view,qs,g.SINGLE)
    def test_single_segment_whitespace_is_not_a_selectable_citation(self):
        view,qs=self.view();qs[0]['text']='甲。\n乙。';qs[0]['ru_end']=len(qs[0]['text'])
        value=g.mock(view,g.SINGLE);value['source_observations'][0]['anchors']=[{'segment_id':'S0002'}]
        with self.assertRaises(ValidationError):g.validate_value(value,view,qs,g.SINGLE)
    def test_single_contract_complete_tree_keeps_child_receipts_and_inactive_status(self):
        sources=self.sources([('剧情对话',[('rich_text','甲出发。乙留下。'*100)])]);sid=next(iter(sources.documents))
        run=self.root/'single';build.prepare_snapshot(run,sources.root,source_ids=[sid],body_limit=200,
            total_request_limit=100,generation_strategy=g.SINGLE)
        result=build.run(run,max_new_requests=100);self.assertEqual(result['sources'][0]['status'],'COMPLETE')
        self.assertEqual(build.audit(run)['reference_integrity'],'PASS')
        doc=next(c for c in read(run/result['sources'][0]['directory']/'cards.json')['cards'] if c['level']=='document')
        self.assertEqual(doc['quote_contract'],contract.SINGLE_CONTRACT)
        self.assertEqual((doc['navigation_status'],doc['human_acceptance']),('INACTIVE','NOT_REVIEWED'))

if __name__=='__main__':unittest.main()
