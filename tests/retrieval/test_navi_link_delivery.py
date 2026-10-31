"""Small Canonical fixtures: reading admission, not model semantics."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval import retrieval_units as ru
from genshin_corpus.retrieval.navi_offline import pipeline,cards
from genshin_corpus.retrieval.navi_offline.common import read,sha,write
from genshin_corpus.retrieval.navi_offline.sources import Sources
from tests.retrieval.test_navi_offline import FIXTURE,fixture


class LinkDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        denied=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        denied.start();self.addCleanup(denied.stop)

    def sources(self,spec):
        cp,_=fixture(self.root)
        manifest=read(cp);template=read(FIXTURE)
        kinds={}
        for unit in template['sections'][0]['units']:kinds.setdefault(unit['kind'],unit)
        for entry in manifest['records']:
            path=Path(entry['canonical_record_path']);record=read(path)
            record['record_metadata']['name']='arbitrary source '+entry['record_id']
            sections=[]
            for n,(label,values) in enumerate(spec):
                section=deepcopy(template['sections'][0]);section['ordinal']=n
                section['source_metadata']['name']=label;section['units']=[]
                for i,(kind,value) in enumerate(values):
                    u=deepcopy(kinds[kind]);u.update(ordinal=i,unit_id=f'unit:{n}:{i}')
                    if kind=='rich_text':u['value']['normalized_text']=value
                    elif kind=='structured_observation':u['value']={'raw':json.dumps(value),'decoded':value}
                    section['units'].append(u)
                section['component_contexts'][0]['child_unit_ordinals']=list(range(len(values)))
                section['component_contexts'][0]['unit_count']=len(values)
                sections.append(section)
            record['sections']=sections;path.write_bytes(canonical_json_bytes(record))
            entry['canonical_record_sha256']=sha(path)
        cp.write_text(json.dumps(manifest),encoding='utf8')
        rp=self.root/'new-ru'
        ru.build_retrieval_units(cp,rp,config=ru.RetrievalUnitBuildConfig(text_fragment_chars=1200,structured_fragment_chars=1200))
        from genshin_corpus.retrieval.navi_offline.sources import prepare_sources
        target=self.root/'sources';prepare_sources(target,cp,rp/'metadata/manifest.json',[x['parsed_identity_key'] for x in manifest['records']])
        result=Sources(target);self.addCleanup(result.close);return result

    def test_story_outside_discovery_precedes_stats_with_same_budget(self):
        story='她保护出航者，解开结界。\n第二段保留人物经历及结局。'
        s=self.sources([('成长数值',[('rich_text',('攻击力：100\n'*150).rstrip())]),
                        ('相关故事',[('structured_observation',{'rich_text':'<p>'+story+'</p>'}),('rich_text',story)])])
        endpoints=list(s.documents)
        discovered=[s.quote(s.rows(k)[0]['unit_id']) for k in endpoints]
        view,binding,audit=pipeline.select_link_evidence(s,endpoints,discovered,body_limit=200,supplement_limit=20)
        for side,source in zip('AB',endpoints):
            self.assertEqual([q['text'] for q in binding.values() if q['source_id']==source],[story])
            self.assertLessEqual(audit['sources'][source]['supplement_chars'],20)
            self.assertFalse(view[side]['coverage']['whole_page_semantic_coverage'])
        for q in binding.values():s.verify_quote(q)
        # The Card and frozen Link prompt are not part of the admission policy.
        self.assertNotIn('arbitrary source',cards.PROMPT)

    def test_novel_artifact_and_equal_text_different_locations_survive(self):
        s=self.sources([('第三卷',[('rich_text','旅人启程。'),('rich_text','旅人返回。')]),
                        ('生之花',[('rich_text','同一句原文'),('rich_text','同一句原文')])])
        endpoints=list(s.documents)
        discovered=[s.quote(s.rows(k)[0]['unit_id']) for k in endpoints]
        view,binding,audit=pipeline.select_link_evidence(s,endpoints,discovered)
        for source in endpoints:
            self.assertEqual(len([q for q in binding.values() if q['source_id']==source]),4)
            self.assertTrue(all(x['disposition']=='DELIVERED_FULL' for x in audit['sources'][source]['units']))
        self.assertEqual(len({q['id'] for q in binding.values()}),8)

    def test_structured_voice_and_dialogue_branches_keep_typed_context(self):
        s=self.sources([('角色语音',[('structured_observation',{'speaker':'甲','text':'那场风暴后，他没有归来。'})]),
                        ('剧情对话',[('dialogue_graph',None)])])
        endpoints=list(s.documents)
        view,binding,audit=pipeline.select_link_evidence(s,endpoints,[])
        self.assertEqual({q['canonical_address']['canonical_unit_kind'] for q in binding.values()},
                         {'structured_observation','dialogue_graph'})
        for q in binding.values():
            s.verify_quote(q);self.assertNotIn('canonical_start',q)
            self.assertEqual(q['structure'],s.row(q['unit_id'])['structure'])
            self.assertEqual(q['nested_selector'],s.row(q['unit_id'])['nested_selector'])
        self.assertTrue(all(x['source_context']['structure'] for side in 'AB' for x in view[side]['snippets']))

    def test_long_story_partial_range_and_unread_are_exact(self):
        s=self.sources([('背景故事',[('rich_text','剧情'*2000)])]);endpoints=list(s.documents)
        view,binding,audit=pipeline.select_link_evidence(s,endpoints,[],body_limit=700,supplement_limit=0)
        for side,source in zip('AB',endpoints):
            self.assertEqual(view[side]['coverage']['delivered_chars'],700)
            self.assertEqual(view[side]['coverage']['partial_ru_count'],1)
            self.assertEqual(sum(u['available_range'][1] for u in audit['sources'][source]['units']),4000)
            self.assertEqual(sum(b-a for u in audit['sources'][source]['units'] for a,b in u['undelivered_ranges']),3300)
        for q in binding.values():s.verify_quote(q)

    def test_oversized_dialogue_is_not_flattened_or_silently_cut(self):
        s=self.sources([('剧情对话',[('dialogue_graph',None)])]);endpoints=list(s.documents)
        with self.assertRaises(ValueError) as caught:
            pipeline.select_link_evidence(s,endpoints,[],body_limit=1,supplement_limit=0)
        for source in endpoints:
            units=caught.exception.delivery_audit['sources'][source]['units']
            self.assertTrue(all(u['disposition']=='OMITTED' for u in units))
            self.assertTrue(all(u['reason']=='WHOLE_DIALOGUE_FRAGMENT_EXCEEDS_BUDGET' for u in units))

    def test_selected_late_story_fragment_is_admitted_before_earlier_context(self):
        s=self.sources([('相关故事',[('rich_text','剧情'*2000)])]);endpoints=list(s.documents)
        anchors=[s.quote(s.rows(source)[-1]['unit_id']) for source in endpoints]
        view,binding,audit=pipeline.select_link_evidence(s,endpoints,anchors,body_limit=700,supplement_limit=0)
        for anchor in anchors:self.assertIn(anchor,binding.values())
        for side in 'AB':self.assertEqual(view[side]['coverage']['delivered_chars'],700)

    def test_foreign_and_forged_quote_rejected_before_admission(self):
        s=self.sources([('正文',[('rich_text','可靠原文')])]);endpoints=list(s.documents)
        q=s.quote(s.rows(endpoints[0])[0]['unit_id']);q['text']='伪造'
        with self.assertRaises(ValueError):pipeline.select_link_evidence(s,endpoints,[q])
        q=s.quote(s.rows(endpoints[0])[0]['unit_id'])
        with self.assertRaises(ValueError):pipeline.select_link_evidence(s,[endpoints[1]],[q])


if __name__=='__main__':unittest.main()
