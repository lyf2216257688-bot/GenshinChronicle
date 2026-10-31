"""Adversarial grounding contracts; mock outcomes are not semantic acceptance."""
from copy import deepcopy
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from jsonschema.exceptions import ValidationError

from genshin_corpus.retrieval.navi_offline import attribution, cards, requests, story_retrieval
from genshin_corpus.retrieval.navi_offline import index
from genshin_corpus.retrieval.navi_offline.common import digest, read
from genshin_corpus.retrieval.navi_offline.testing import MockEncoder
from tests.retrieval import test_navi_link_delivery as delivery_tests


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        p.start();self.addCleanup(p.stop)
        self.view={s:{'title':s,'snippets':[{'id':s+'1','text':t}]} for s,t in
                   [('A','如果教我射术的人没有出征，我们的儿子应是少年的年纪。'),('B','另一位巫女随海祇出征。')]}
        self.value={'propositions':[{'id':'p1','text':'或可推测共同背景。','reason':'仍待核',
                    'boundary':'不是身份同一','increment_a_from_b':'少年随将军出征。',
                    'increment_b_from_a':'另一端阵营不能迁移','evidence_gap':'尚无阵营对应',
                    'critical_error':None,'story':{'bridge':'隐含线索仍需证据'}}]}
        self.av=attribution.prepare(self.view,{'generation':self.value,'review':deepcopy(self.value)})

    def test_all_prose_including_increment_review_and_bridge_bound(self):
        self.assertEqual(len(self.av['claims']),14)
        self.assertEqual({c['origin'] for c in self.av['claims']},{'generation','review'})
        self.assertIn('increment_a_from_b',{c['field'] for c in self.av['claims']})
        self.assertNotIn('grade',str(self.av))
        revised=deepcopy(self.value);revised['propositions'][0]['increment_a_from_b']='原主体未知'
        self.assertNotEqual(self.av,attribution.prepare(self.view,{'generation':revised,'review':self.value}))

    def test_missing_duplicate_foreign_field_rejected(self):
        good=attribution.mock(self.av)
        for mutate in (lambda v:v['checks'].pop(),lambda v:v['checks'].append(v['checks'][0]),
                       lambda v:v['checks'][0].update(claim_id='foreign')):
            v=deepcopy(good);mutate(v)
            with self.assertRaises(ValueError):attribution.validate(v,self.av)

    def test_opposite_source_and_forged_quote_rejected(self):
        good=attribution.mock(self.av)
        for change in ({'side':'B'},{'quote':'not source'},{'start':-1},{'end':999}):
            v=deepcopy(good);v['facts'][0]['anchors'][0].update(change)
            with self.assertRaises((ValueError,ValidationError)):attribution.validate(v,self.av)

    def test_no_cross_reference_requirement_or_automatic_promotion(self):
        v=attribution.mock(self.av)
        for check in v['checks']:check['verdict']='INFERENCE'
        attribution.validate(v,self.av)
        self.assertEqual(attribution.disposition(v,backend='live'),'MODEL_CROSSCHECK_ONLY_NOT_HUMAN_ACCEPTANCE')
        v['checks'][0]['verdict']='ERROR'
        self.assertEqual(attribution.disposition(v,backend='live'),'MODEL_DIAGNOSTIC_RISK_NOT_ADMISSION_GATE')
        self.assertEqual(attribution.disposition(None,backend='live'),'NOT_CHECKED_DIAGNOSTIC_ONLY')

    def test_raw_first_mock_replay_no_network_and_no_overwrite(self):
        wire=attribution.wire(self.av);folder=self.root/'jobs/one'
        result=requests.execute(folder,'attribution',wire,self.av,{},backend='mock',mock_value=attribution.mock(self.av))
        self.assertEqual(result['state'],'CONTENT_VERIFIED')
        out=self.root/'replay'
        replay=requests.execute(out,'attribution',wire,self.av,{},backend='replay',replay_root=self.root/'jobs')
        self.assertEqual(replay['state'],'CONTENT_VERIFIED')
        self.assertEqual(read(folder/'value.json'),read(out/'value.json'))

    def test_archived_positive_interpretation_and_negative_increment_are_preserved(self):
        control=read(Path(__file__).parents[1]/'fixtures/retrieval/navi-attribution-archive-controls.json')
        positive=control['positive'];negative=control['negative']
        av=attribution.prepare(positive['view'],positive['outputs'])
        p=positive['outputs']['generation']['propositions'][0]
        self.assertEqual(p['support'],'EXPLICIT')
        self.assertEqual(p['release'],'ELIGIBLE_PENDING_RELEASE')
        self.assertIn('二度飞天',p['text'])
        self.assertTrue(any(c['field']=='story.bridge' for c in av['claims']))
        # Oracle labels test admissible contract states, not model comprehension.
        oracle=attribution.mock(av)
        for check in oracle['checks']:check['verdict']='INFERENCE'
        attribution.validate(oracle,av)
        self.assertEqual(attribution.disposition(oracle,backend='replay'),
                         'MODEL_CROSSCHECK_ONLY_NOT_HUMAN_ACCEPTANCE')
        bad=attribution.prepare(negative['view'],negative['outputs'])
        self.assertTrue(any(c['field'].startswith('increment_') and '左卫门' in c['text'] and
                            '随将军出征' in c['text'] for c in bad['claims']))
        self.assertEqual(positive['outputs']['generation']['propositions'][0],p)

    def test_neighbor_error_holds_only_its_proposition_without_promoting_other(self):
        other=deepcopy(self.value['propositions'][0]);other['id']='p2'
        view=attribution.prepare(self.view,{'generation':{'propositions':self.value['propositions']+[other]}})
        oracle=attribution.mock(view)
        claim_map={c['claim_id']:c for c in view['claims']}
        for c in oracle['checks']:
            c['verdict']='ERROR' if claim_map[c['claim_id']]['proposition_id']=='p1' else 'INFERENCE'
        findings=attribution.proposition_findings(oracle,view,backend='replay')
        self.assertEqual(findings[0]['disposition'],'MODEL_DIAGNOSTIC_RISK_NOT_ADMISSION_GATE')
        self.assertEqual(findings[1]['disposition'],'MODEL_CROSSCHECK_ONLY_NOT_HUMAN_ACCEPTANCE')
        self.assertTrue(all(f['human_acceptance']=='NOT_REVIEWED' for f in findings))


class StoryRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        p.start();self.addCleanup(p.stop)

    sources=delivery_tests.LinkDeliveryTests.sources

    def leaf(self,s,source,section):
        qs=[s.quote(r['unit_id']) for r in s.rows(source) if r['source']['canonical_address']['section_ordinal']==section]
        v=cards.mock(cards.card_view('任意资料',qs))
        v['source_observations']=[{'text':'本节内容','evidence_ids':[q['id'] for q in qs]}]
        v['queries']=[{'text':'失踪人物的出征经过','evidence_ids':[q['id'] for q in qs]}]
        return cards.make_card(source,'任意资料','section',None,[{'job_id':digest(section),'value':v}],qs,section=section)

    def test_mechanics_and_inherited_document_copies_do_not_vote(self):
        s=self.sources([('成长数值',[('rich_text','攻击力100，升级用素材。')]),('相关故事',[('rich_text','她回忆旧友出征。')])])
        source=next(iter(s.documents));mechanics=self.leaf(s,source,0);story=self.leaf(s,source,1)
        doc=deepcopy(story);doc.update(card_id='doc',level='document',overview='攻击力9999')
        doc['coverage']['derived_from_cards']=True
        clue=deepcopy(story);clue.update(card_id='clue',level='clue')
        original=deepcopy([doc,mechanics,story,clue])
        result=story_retrieval.prepare_views(original,s)
        self.assertEqual([i['id'] for i in result['items']],[story['card_id']])
        self.assertEqual(len(result['queries']),1)
        self.assertNotIn('攻击力',result['items'][0]['text'])
        self.assertEqual(original,[doc,mechanics,story,clue])

    def test_hypotheses_and_mixed_overview_stored_but_not_embedded(self):
        s=self.sources([('小说卷一',[('rich_text','如果旧友未出征，我们的孩子应当已成年。')])])
        c=self.leaf(s,next(iter(s.documents)),0);c['overview']='攻击力大幅增长'
        c['hypotheses']=[{'text':'少年与旧友身份同一','evidence_ids':[c['evidence'][0]['id']]}]
        result=story_retrieval.prepare_views([c],s)
        self.assertNotIn('身份同一',result['items'][0]['text'])
        self.assertNotIn('攻击力',result['items'][0]['text'])
        self.assertIn('source_observations:',result['items'][0]['text'])
        self.assertTrue(c['hypotheses'])

    def test_same_query_across_direct_leaves_votes_once_per_source(self):
        s=self.sources([('小说卷一',[('rich_text','旧友出征。')]),('小说卷二',[('rich_text','少年失踪。')])])
        cs=[self.leaf(s,src,n) for src in s.documents for n in (0,1)]
        result=story_retrieval.prepare_views(cs,s)
        self.assertEqual(len(result['queries']),2)
        self.assertEqual(sum(q['reason']=='DUPLICATE_SOURCE_QUERY' for c in result['audit'] for q in c['queries']),2)
        self.assertTrue(all(len(q['evidence_ids'])==2 for q in result['queries']))

    def test_source_budget_normalizes_query_count(self):
        s=self.sources([('小说',[('rich_text','失散人物与事件经过。')])])
        cs=[self.leaf(s,src,0) for src in s.documents]
        cs[0]['queries'] += [{'text':'另一个具体事件', 'evidence_ids':[cs[0]['evidence'][0]['id']]}]
        result=story_retrieval.prepare_views(cs,s)
        for src in s.documents:
            self.assertAlmostEqual(sum(q['rrf_weight'] for q in result['queries'] if q['source_id']==src),1)

    def test_projection_cannot_resume_or_open_as_legacy_or_ru(self):
        enc=MockEncoder();items=[{'id':'card','source_id':'source','text':'原文观察和待查线索'}]
        root=self.root/'index'
        index.build(root,items,enc,kind='card',input_identity='fixture',projection=story_retrieval.PROJECTION)
        with self.assertRaises(ValueError):index.build(root,items,enc,kind='card',input_identity='fixture')
        with self.assertRaises(ValueError):index.Index(root,kind='card',encoder_space=enc.space_identity,projection='card_semantic_labeled_v1')
        with self.assertRaises(ValueError):index.build(self.root/'ru',items,enc,kind='ru',input_identity='fixture',projection=story_retrieval.PROJECTION)

    def test_unknown_card_keeps_ru_fallback_and_cross_source_rejected(self):
        s=self.sources([('基础信息',[('structured_observation',{'name':'任意资料','star':4})])])
        c=self.leaf(s,next(iter(s.documents)),0)
        result=story_retrieval.prepare_views([c],s)
        self.assertEqual(result['items'],[])
        self.assertEqual(result['ru_fallback'],'UNCHANGED_EXACT_RU_INDEX')
        c['source_id']=list(s.documents)[1]
        with self.assertRaises(ValueError):story_retrieval.prepare_views([c],s)

    def test_mixed_description_keeps_exact_legend_tail_not_stats(self):
        control=read(Path(__file__).parents[1]/'fixtures/retrieval/navi-attribution-archive-controls.json')
        text=next(q['text'] for q in control['positive']['view']['B']['snippets'] if q['id']=='B001')
        s=self.sources([('装备描述',[('rich_text',text)])])
        c=self.leaf(s,next(iter(s.documents)),0)
        eid=c['evidence'][0]['id']
        c['queries']=[{'text':'御剑公子为何二度飞天','evidence_ids':[eid]},
                      {'text':'普通攻击提高多少攻击力','evidence_ids':[eid]}]
        result=story_retrieval.prepare_views([c],s)
        self.assertIn('二度飞天',result['items'][0]['text'])
        self.assertNotIn('攻击力',result['items'][0]['text'])
        self.assertNotIn('0.5',result['items'][0]['text'])
        self.assertEqual([q['text'] for q in result['queries']],['御剑公子为何二度飞天'])
        span=result['audit'][0]['description_ranges'][0]
        q=s.quote(span['unit_id'],span['ru_start'],span['ru_end'])
        self.assertEqual(q['text'],span['text'])
        self.assertIn('普通攻击',c['evidence'][0]['text'])

    def test_unknown_and_mechanical_description_not_declared_narrative(self):
        self.assertEqual(story_retrieval.description_ranges('精炼1阶\n昔日的技能提高20%攻击力。\n可靠的剑。'),[])
