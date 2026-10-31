"""No-provider regression: exact quotes, accounting, adaptive Card-only recovery."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import httpx

from genshin_corpus.retrieval.navi_offline import attribution, cards_v2 as v2, card_build, card_census, index, requests
from genshin_corpus.retrieval.navi_offline.anchors import locate
from genshin_corpus.retrieval.navi_offline.common import read, sha
from genshin_corpus.retrieval.navi_offline.sources import Sources
from genshin_corpus.retrieval.navi_offline.testing import MockEncoder
from tests.retrieval.test_navi_offline import fixture
from tests.retrieval.test_navi_link_delivery import LinkDeliveryTests


class CardBuildTests(unittest.TestCase):
    sources=LinkDeliveryTests.sources

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('no network'));p.start();self.addCleanup(p.stop)

    def test_unique_exact_offset_never_fuzzy_or_model_counted(self):
        self.assertEqual(locate('甲😀，乙。','😀，乙'),(1,4))
        for text,quote in [('重复重复','重复'),('甲乙','甲 乙'),('甲乙','')]:
            with self.assertRaises(ValueError):locate(text,quote)

    def test_auxiliary_offsets_owned_by_program_with_legacy_replay(self):
        view={s:{'snippets':[{'id':s+'1','text':s+'😀真实片段。'}]} for s in 'AB'}
        av=attribution.prepare(view,{'generation':{'propositions':[]}})
        value=attribution.mock(av)
        self.assertNotIn('start',value['facts'][0]['anchors'][0])
        accepted=attribution.validate(value,av)
        self.assertEqual(accepted['facts'][0]['anchors'][0]['end'],len('A😀真实片段。'))
        old=deepcopy(av);old['version']='source-local-attribution-v1'
        self.assertEqual(attribution.validate(accepted,old),accepted)
        accepted['facts'][0]['anchors'][0]['start']=1
        with self.assertRaises(ValueError):attribution.validate(accepted,old)

    def test_provider_overcap_keeps_usage_rejects_content_and_stops_dispatch(self):
        view={'title':'fixture','evidence':[]};wire=requests.cards.wire(view,cap=10)
        events=requests.mock_events(json.dumps(requests.cards.mock(view)))
        events[-1]['response']['usage']={'input_tokens':20,'output_tokens':12,'total_tokens':32}
        raw=''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode()
        class Body(httpx.SyncByteStream):
            def __iter__(self):yield raw
        transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Body()))
        folder=self.root/'jobs/one'
        result=requests.execute(folder,'card',wire,view,[],backend='mock',transport_override=transport)
        self.assertEqual(result['usage']['total_tokens'],32)
        self.assertEqual(result['output_limit']['status'],'EXCEEDED')
        self.assertEqual(result['state'],'STREAM_OR_CONTENT_INVALID')
        self.assertFalse((folder/'value.json').exists())
        self.assertEqual(requests.accounted_tokens(folder),result['usage']['total_tokens'])
        replay=requests.execute(self.root/'replay','card',wire,view,[],backend='replay',replay_root=folder.parent)
        self.assertEqual(replay['usage'],result['usage'])
        self.assertEqual(replay['state'],'REPLAY_INVALID')

    def test_known_usage_survives_json_rejection(self):
        view={'title':'fixture','evidence':[]};wire=requests.cards.wire(view)
        result=requests.execute(self.root/'bad','card',wire,view,[],backend='mock',mock_value={'invalid':'schema'},ledger=self.root/'ledger')
        self.assertEqual(result['usage']['total_tokens'],5)
        self.assertEqual(result['output_limit']['status'],'PASS')
        self.assertEqual(result['state'],'STREAM_OR_CONTENT_INVALID')

    def test_narrative_only_selection_and_typed_voice_branch_context(self):
        s=self.sources([('成长数值',[('rich_text','攻击力100')]),('相关故事',[('rich_text','回忆中的教射术者出征；孩子仅是假设中的比较对象。'),
            ('structured_observation',{'text':'副本表示不自动等同原文'})]),('角色语音',[('structured_observation',{'speaker':'甲','text':'我未曾离开'})]),
            ('剧情对话',[('dialogue_graph',None)])])
        src=next(iter(s.documents));p=v2.reading_plan(s,src)
        self.assertTrue(any(u['reason']=='NON_NARRATIVE_SECTION_RETAINED' for u in p['units']))
        qs=p['selected'];self.assertNotIn('攻击力',''.join(q['text'] for q in qs))
        self.assertTrue(any(q['canonical_address']['canonical_unit_kind']=='dialogue_graph' for q in qs))
        for q in qs:s.verify_quote(q)

    def test_v2_exact_observation_grounding_and_fact_hypothesis_separation(self):
        s=self.sources([('小说',[('rich_text','陌生人归来，孩子留在原地。')])]);src=next(iter(s.documents))
        p=v2.reading_plan(s,src);_,view,qs=next(v2.read_batches(s,src,p))
        raw=v2.mock(view);value=v2.validate(raw,view,qs)
        self.assertNotIn('start',raw['source_observations'][0]['anchors'][0])
        value['hypotheses']=[{'text':'未证身份推测','evidence_ids':[qs[0]['id']]}]
        c=v2.make_card(s,src,'document',None,{'job_id':'test','value':value},qs,plan=p)
        text=v2.projection(c)
        self.assertIn('HYPOTHESIS_NOT_FACT: 未证身份推测',text)
        self.assertNotIn('read_ids',text)
        bad=deepcopy(raw);bad['source_observations'][0]['anchors'][0]['quote']='出航'
        with self.assertRaises(ValueError):v2.validate(bad,view,qs)

    def test_synthesis_is_new_response_not_join_and_inherited_only(self):
        s=self.sources([('第一章',[('rich_text','甲出发。')]),('第二章',[('rich_text','乙归来。')])])
        src=next(iter(s.documents));p=v2.reading_plan(s,src);children=[]
        for section,view,qs in v2.read_batches(s,src,p,body_limit=4):
            val=v2.validate(v2.mock(view),view,qs)
            children.append(v2.make_card(s,src,'section',section,{'job_id':str(section)+qs[0]['id'],'value':val},qs,plan=p))
        view=v2.synthesis_view('fixture',children,{'unread':True});raw=v2.mock(view)
        self.assertNotIn('ru_start',view['children'][0]['source_observations'][0]['anchors'][0])
        self.assertIn('ru_start',children[0]['source_observations'][0]['anchors'][0])
        raw['overview']='独立归纳占位，不是原批次摘要拼接。'
        qs=list({q['id']:q for c in children for q in c['evidence']}.values())
        val=v2.validate(raw,view,qs)
        doc=v2.make_card(s,src,'document',None,{'job_id':'synth','value':val},qs,children=children,plan=p)
        self.assertEqual(doc['overview'],raw['overview'])
        self.assertEqual(doc['coverage']['direct_delivered_ids'],[])
        self.assertEqual(doc['coverage']['model_read_ids'],[])
        self.assertEqual(len(doc['coverage']['model_child_ids']),len(children))
        self.assertFalse(doc['coverage']['whole_page_semantic_coverage'])
        raw['source_observations'][0]['anchors'][0]['quote']='甲'
        with self.assertRaises(ValueError):v2.validate(raw,view,qs)

    def test_card_only_batch_resume_reuse_audit_and_separate_index(self):
        cp,rp=fixture(self.root);root=self.root/'build'
        card_build.prepare(root,cp,rp,['0','1'],body_limit=80)
        initial=card_build.run(root,max_new_requests=1)
        self.assertTrue(any(s['status']!='COMPLETE' for s in initial['sources']))
        job=next((root/'jobs').glob('*/complete.json'));before=sha(job)
        result=card_build.run(root,max_new_requests=100)
        self.assertTrue(all(s['status']=='COMPLETE' for s in result['sources']))
        self.assertEqual(sha(job),before)
        self.assertEqual(card_build.run(root)['new_sdk_calls'],0)
        self.assertEqual(card_build.audit(root)['archive_integrity'],'PASS')
        self.assertTrue(all(read(p)['stage']=='card' for p in (root/'jobs').glob('*/plan.json')))
        enc=MockEncoder();card_build.build_index(root,self.root/'vectors',enc)
        card_build.prepare(self.root/'next',cp,rp,['0','1'],body_limit=80)
        reused=card_build.run(self.root/'next',max_new_requests=100,reuse=[root])
        self.assertEqual(reused['new_sdk_calls'],0)
        self.assertEqual(card_build.audit(self.root/'next')['archive_integrity'],'PASS')
        self.assertEqual([c['card_id'] for c in card_build.iter_cards(root)],
                         [c['card_id'] for c in card_build.iter_cards(self.root/'next')])

    def test_census_accounts_all_fixture_records_and_no_credential_access(self):
        cp,rp=fixture(self.root)
        summary=card_census.census(self.root/'census',cp,rp)
        self.assertEqual(summary['records'],2)
        self.assertEqual(summary['quarantined'],0)
        self.assertEqual(summary['paid_requests'],0)

    def test_incremental_vectors_only_encode_new_windows_and_reject_foreign(self):
        enc=MockEncoder();items=[{'id':str(i),'source_id':'s','text':'原文'+str(i)} for i in range(5)]
        a=self.root/'a';b=self.root/'b'
        index.build(a,items,enc,kind='card',input_identity='a',projection=v2.PROJECTION,shard_size=2)
        changed=deepcopy(items);changed[-1]['text']='新增内容'
        with patch.object(enc,'encode_documents',wraps=enc.encode_documents) as encode:
            index.build(b,changed,enc,kind='card',input_identity='b',projection=v2.PROJECTION,shard_size=2,reuse_from=a)
            self.assertEqual(sum(len(c.args[0]) for c in encode.call_args_list),1)
        with self.assertRaises(ValueError):index.build(self.root/'foreign',items,enc,kind='card',input_identity='c',reuse_from=a)
        with self.assertRaises(ValueError):card_build.prepare(self.root/'rev','missing','missing',revision='fix')

    def test_targeted_regeneration_reuses_other_reading_jobs(self):
        cp,rp=fixture(self.root);first=self.root/'first';second=self.root/'second'
        card_build.prepare(first,cp,rp,['0'],body_limit=8)
        card_build.run(first,max_new_requests=100)
        plans=[p for p in (first/'jobs').glob('*/plan.json') if read(p)['view']['mode']=='read']
        self.assertGreater(len(plans),1)
        card_build.prepare(second,cp,rp,['0'],body_limit=8,revision='fix1',reason='test only',regenerate_jobs=[plans[0].parent.name])
        result=card_build.run(second,reuse=[first],max_new_requests=100)
        self.assertTrue(all(s['status']=='COMPLETE' for s in result['sources']))
        new_read=[p for p in (second/'jobs').glob('*/plan.json') if read(p)['view']['mode']=='read' and read(p)['backend']=='mock']
        self.assertEqual(len(new_read),1)
        self.assertEqual(read(new_read[0])['view']['build_revision'],'fix1')

    def test_model_context_compacts_archival_refs_without_losing_branch_edges(self):
        s=self.sources([('剧情对话',[('dialogue_graph',None)])]);source=next(iter(s.documents))
        q=s.quote(s.rows(source)[0]['unit_id']);before=deepcopy(q)
        q['structure']['dialogue']['node_raw_ref']={'artifact_path':'x'*30000}
        model=v2.model_excerpt(q)
        self.assertLess(len(json.dumps(model)),len(json.dumps(q)))
        self.assertNotIn('node_raw_ref',str(model))
        self.assertEqual(model['structure']['dialogue']['observed_edges'],[
            {k:v for k,v in e.items() if k not in {'raw_ref','source_position'}}
            for e in before['structure']['dialogue']['observed_edges']])


if __name__=='__main__':unittest.main()
