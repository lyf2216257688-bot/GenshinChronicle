"""Offline evidence/admission regressions, never a model semantic score."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import ValidationError
from genshin_corpus.retrieval.navi_offline import (
    anchors, cards, cards_v2 as v2, card_generation as g, card_semantics as s,
    card_build as build, card_structure, card_recovery)
from genshin_corpus.retrieval.navi_offline.common import read, digest
from tests.retrieval import test_navi_link_delivery as delivery_fixtures

FIXTURE=Path(__file__).parents[1]/'fixtures/retrieval/card-semantic-cases-20261102.json'


def case_view(case):
    evidence=[]
    for q in case['quotes']:
        evidence.append(dict(id=digest([q['evidence_id'],q['ru_start'],q['ru_end']]),
            text=q['text'],source_id=case['source_id'],canonical_sha256=q['canonical_sha256'],
            unit_id=q['unit_id'],ru_start=q['ru_start'],ru_end=q['ru_end'],
            canonical_address={'canonical_unit_kind':q['kind'],'section_ordinal':q['section'],
                'canonical_unit_ordinal':0},nested_selector=q['selector'],structure=q['structure'],
            content_role=q['content_role'],provenance=q['provenance']))
    return dict(mode='read',title=case['name'],scope={'selection':'EXACT_AUDIT_EXCERPTS_ONLY'},
        evidence=[v2.model_excerpt(q) for q in evidence],children=[]),evidence


def child_from_quotes(view, qs):
    shown=g.model_view(view,g.SEMANTIC)
    value=g.mock(view,g.SEMANTIC)
    # Mechanical echo builds valid inherited anchors, not semantic findings.
    value['source_observations']=[]
    for e in shown['evidence']:
        ids=[x['id'] for x in e['segments'] if x['text'].strip()]
        value['source_observations'].append(dict(text='SOURCE_ECHO_NOT_SEMANTIC_JUDGMENT',
            subject='UNKNOWN',action='ECHO',object='UNKNOWN',modality='MOCK_NOT_SEMANTIC',
            anchors=[{'segment_id':i} for i in ids]))
    accepted=g.validate_value(value,view,qs,g.SEMANTIC)
    return dict(card_id=digest(qs),level='section',section=0,overview=accepted['overview'],
        evidence=qs,**{f:accepted[f] for f in cards.FIELDS})


class SemanticWireTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('no network'))
        p.start();self.addCleanup(p.stop)

    def test_six_real_cases_deliver_exact_quotes_before_fallible_child_judgments(self):
        cases=read(FIXTURE)['cases']
        self.assertEqual({c['case_id'] for c in cases},{'3470','399','507877','2287','4943','3076'})
        for case in cases:
            with self.subTest(case=case['name']):
                view,qs=case_view(case);child=child_from_quotes(view,qs)
                child['hypotheses']=[{'text':'子卡错误也可能存在；不可当作独立证据',
                    'evidence_ids':[qs[0]['id']]}]
                before=deepcopy(child)
                synth=s.synthesis_context(v2.synthesis_view(case['name'],[child],view['scope'],
                    preserve_locations=True),[child])
                wire=g.wire(synth,g.SEMANTIC);shown=json.loads(wire['messages'][1]['content'])
                self.assertLess(list(shown).index('inherited_anchors'),list(shown).index('children'))
                self.assertLess(list(shown).index('source_context'),list(shown).index('children'))
                self.assertEqual(shown['children'][0]['hypotheses'][0]['text'],child['hypotheses'][0]['text'])
                # All source prose, including conditionals and attribution, survives.
                for q in qs:
                    actual=''.join(a['quote'] for a in shown['inherited_anchors']
                        if a['evidence_id']==g.aliases(synth)[1][q['id']])
                    self.assertEqual(actual,q['text'])
                self.assertEqual(child,before)
                self.assertEqual(wire['model'],'deepseek-v4.1-flash-1017')
                self.assertEqual(wire['max_tokens'],32000)
                # Corrected judgments may cite the original even if child doubts it.
                output=g.mock(synth,g.SEMANTIC)
                output['overview']=case['reference_judgment']
                accepted=g.validate_value(output,synth,qs,g.SEMANTIC)
                self.assertEqual(accepted['source_observations'][0]['anchors'][0]['quote'],
                    shown['inherited_anchors'][0]['quote'])

    def test_local_identity_does_not_hide_different_story_contexts(self):
        case=next(c for c in read(FIXTURE)['cases'] if c['case_id']=='4943')
        view,qs=case_view(case);child=child_from_quotes(view,qs)
        shown=g.model_view(s.synthesis_context(v2.synthesis_view('',[child],{},preserve_locations=True),[child]),g.SEMANTIC)
        sections={q['section'] for q in shown['source_context']}
        self.assertGreater(len(sections),1)
        self.assertEqual({a['evidence_id'] for a in shown['inherited_anchors']},
            {q['id'] for q in shown['source_context']})

    def test_unknown_provenance_is_preserved_not_labeled_official_by_policy(self):
        view,qs=case_view(read(FIXTURE)['cases'][0]);child=child_from_quotes(view,qs)
        shown=g.model_view(s.synthesis_context(v2.synthesis_view('',[child],{},preserve_locations=True),[child]),g.SEMANTIC)
        self.assertEqual(shown['source_context'][0]['provenance']['state'],'unknown')
        self.assertNotIn('official',json.dumps(shown['source_context']))

    def test_wrong_ids_fail_without_guessing_and_nonobservations_also_need_anchors(self):
        view,qs=case_view(read(FIXTURE)['cases'][0]);raw=g.mock(view,g.SEMANTIC)
        for bad in ('S9999','E001','S1'):
            value=deepcopy(raw);value['source_observations'][0]['anchors']=[{'segment_id':bad}]
            with self.assertRaises((ValueError,ValidationError)):g.validate_value(value,view,qs,g.SEMANTIC)
        raw['hypotheses']=[{'text':'未证推测','anchors':[]}]
        with self.assertRaises(ValidationError):g.validate_value(raw,view,qs,g.SEMANTIC)

    def test_mechanical_acceptance_still_cannot_certify_semantic_truth(self):
        case=next(c for c in read(FIXTURE)['cases'] if c['case_id']=='2287')
        view,qs=case_view(case);raw=g.mock(view,g.SEMANTIC)
        raw['source_observations'][0]['text']='错误示范：巫女确有儿子。'
        value=g.validate_value(raw,view,qs,g.SEMANTIC)
        # Deliberately accepted mechanically: no fake keyword semantic judge.
        self.assertEqual(value['source_observations'][0]['text'],raw['source_observations'][0]['text'])

    def test_new_policy_is_opt_in_and_keeps_atomic_quote_contract(self):
        view,_=case_view(read(FIXTURE)['cases'][0])
        self.assertEqual(g.DEFAULT_STRATEGY,g.EVIDENCE_32K)
        self.assertNotIn(s.POLICY,g.wire(view,g.SINGLE)['messages'][1]['content'])
        self.assertEqual(g.quote_contract(g.SEMANTIC),g.quote_contract(g.SINGLE))
        prompt=g.prompt(g.SEMANTIC)
        self.assertNotIn('合成时不可创造子卡未有的事实',prompt)
        self.assertIn('原文直接陈述',prompt);self.assertIn('人物或文献声称',prompt)
        self.assertIn('合理推断',prompt);self.assertIn('缺乏证据',prompt)
        self.assertIn('反事实',prompt);self.assertIn('歌谣',prompt)


class SemanticSelectionTests(unittest.TestCase):
    sources=delivery_fixtures.LinkDeliveryTests.sources
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('no network'));p.start();self.addCleanup(p.stop)

    def test_noise_filter_requires_positive_surface_evidence(self):
        for text,reason in [('暂无','PLACEHOLDER'),('100.5%','PURE_NUMERIC'),
            ('/list/0/root_id\tewllh0qqr0dT1711546416025','TECHNICAL_SELECTOR'),
            ('结束阶段：造成1点岩元素伤害[伤害]。\n可用次数[可用次数]：2','EXPLICIT_MECHANIC_RULE'),
            ('现实时间四天完成基斯庇的委托后直接接取该任务','EXPLICIT_WALKTHROUGH')]:
            self.assertEqual(s.noise_reason(text),reason)
        for text in ['基斯庇','暂无人知晓他的去向。','她造成了无法弥补的伤害，随后离开故乡。',
            '前往灯塔保护村民。','造成1点伤害。\n她在战后归还旧弓。',
            '消耗100年的生命，换回了故乡的和平。','每回合，她都会回想离开的友人。']:
            self.assertIsNone(s.noise_reason(text))
        self.assertIsNone(s.noise_reason('结束阶段：造成1点伤害。',dialogue=True))

    def test_equipment_prose_survives_without_story_keywords_and_with_damage_words(self):
        source=self.sources([('装备描述',[('rich_text',
            '攻击力：100\n她留下的伤害至今未愈，故人仍在塔下等候。\n「我会回到灯塔与你重逢。」\n「快走！」\n暂无')])])
        sid=next(iter(source.documents));old=v2.reading_plan(source,sid)
        plan=card_structure.plan(source,sid,group_modules=True,semantic_selection=True)
        selected=''.join(q['text'] for q in plan['selected'])
        self.assertIn('伤害至今未愈',selected);self.assertIn('「我会回到灯塔与你重逢。」',selected)
        self.assertIn('「快走！」',selected)
        self.assertNotIn('攻击力：100',selected);self.assertNotIn('暂无',selected)
        self.assertLess(sum(len(q['text']) for q in old['selected']),len(selected))
        for q in plan['selected']:source.verify_quote(q)
        self.assertTrue(any(u.get('narrative_omissions') for u in plan['units']))

    def test_placeholders_and_technical_ids_are_audited_with_no_invented_plot(self):
        source=self.sources([('未知正文',[('rich_text','暂无'),('rich_text','12345'),
            ('structured_observation',{'root_id':'abc123'})])])
        sid=next(iter(source.documents));plan=card_structure.plan(source,sid,semantic_selection=True)
        self.assertNotIn('暂无',''.join(q['text'] for q in plan['selected']))
        self.assertTrue(all(u['selected_ranges']==[] for u in plan['units']))
        for u in plan['units']:self.assertEqual(u['unread_ranges'],[[0,u['chars']]])

    def test_new_strategy_mock_build_replays_dependencies_and_does_not_reuse_old_semantics(self):
        source=self.sources([('第一章',[('rich_text','甲出发。乙留守。')]),
            ('第二章',[('rich_text','乙离开。甲归来。')])])
        old=self.root/'old';build.prepare_snapshot(old,source.root,total_request_limit=100,generation_strategy=g.SINGLE)
        build.run(old,max_new_requests=100)
        new=self.root/'new';build.prepare_snapshot(new,source.root,total_request_limit=100,generation_strategy=g.SEMANTIC)
        result=build.run(new,max_new_requests=100,reuse=[old])
        self.assertGreater(result['new_sdk_calls'],0)  # Mock only, old source cannot silently skip new policy.
        self.assertTrue(all(x['status']=='COMPLETE' for x in result['sources']))
        self.assertEqual(build.audit(new)['document_integrity'],'PASS')
        for row in result['sources']:
            folder=new/row['directory']
            self.assertEqual(card_recovery.validate_dependencies(new,folder,row['source_id'],source)['status'],'PASS')
            doc=next(c for c in read(folder/'cards.json')['cards'] if c['level']=='document')
            self.assertIsNone(doc['coverage']['whole_page_semantic_coverage'])
            self.assertEqual(doc['coverage']['semantic_coverage_status'],'NOT_ASSESSED')
            self.assertEqual(doc['coverage']['selected_scope_delivery']['status'],'COMPLETE')
        self.assertEqual(build.run(new)['new_sdk_calls'],0)

    def test_coverage_separates_delivery_unknown_semantics_and_empty_selection(self):
        plan={'selected':[dict(unit_id='u',ru_start=10,ru_end=20),dict(unit_id='u',ru_start=15,ru_end=25)]}
        full=s.delivery_coverage(plan,{'u':[[0,19],[19,25]]})
        self.assertEqual(full['status'],'COMPLETE');self.assertEqual(full['required_chars'],15)
        self.assertEqual(full['semantic_assessment'],'NOT_ASSESSED')
        partial=s.delivery_coverage(plan,{'u':[[11,20],[12,21]]})
        self.assertEqual(partial['delivered_or_inherited_chars'],10)
        self.assertEqual(partial['undelivered_selected_ranges'],[dict(unit_id='u',range=[10,11]),dict(unit_id='u',range=[21,25])])
        self.assertEqual(s.delivery_coverage({'selected':[]},{})['status'],'EMPTY_SELECTION')
        self.assertEqual(s.delivery_coverage(None,{})['status'],'NOT_PLANNED')


if __name__=='__main__':unittest.main()
