"""Provider-free evidence selection and source-independent Card export."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
from jsonschema import ValidationError

from genshin_corpus.retrieval.navi_offline import anchors, card_generation as g, cards_v2 as v2
from genshin_corpus.retrieval.navi_offline import card_build as build, card_structure as structure
from genshin_corpus.retrieval.navi_offline.common import read, sha, verify
from genshin_corpus.retrieval.navi_offline.testing import MockEncoder
from tests.retrieval import test_navi_link_delivery as delivery


class CardEvidenceTests(unittest.TestCase):
    sources=delivery.LinkDeliveryTests.sources

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'));p.start();self.addCleanup(p.stop)

    def reading(self,text='重复。重复。\r\n甲😀！\\n字面量。'):
        q={'id':'evidence','text':text,'source_id':'source','canonical_sha256':'snapshot','unit_id':'ru',
           'ru_start':10,'ru_end':10+len(text)}
        view={'mode':'read','title':'fixture','children':[],'evidence':[q]}
        return view,[q]

    def prepared(self):
        s=self.sources([('故事',[('rich_text','甲出发。乙留下。')])])
        root=self.root/'run';build.prepare_snapshot(root,s.root,total_request_limit=100)
        return root

    def test_lossless_segments_and_repeated_occurrence_coordinates(self):
        view,qs=self.reading();ss=anchors.aliased_segments(qs)[qs[0]['id']]
        self.assertEqual(''.join(s['text'] for s in ss),qs[0]['text'])
        wire=g.wire(view,g.EVIDENCE_32K);mv=json.loads(wire['messages'][1]['content'])
        self.assertNotIn('text',mv['evidence'][0]);self.assertEqual(mv['quote_contract'],anchors.CONTRACT)
        value=g.mock(view,g.EVIDENCE_32K)
        value['source_observations'][0]['anchors']=[{'evidence_id':'E001','start_segment_id':ss[1]['id'],'end_segment_id':ss[1]['id']}]
        accepted=g.validate_value(value,view,qs,g.EVIDENCE_32K)
        a=accepted['source_observations'][0]['anchors'][0]
        self.assertEqual((a['quote'],a['start'],a['ru_start']),('重复。',3,13))
        with self.assertRaises(ValueError):anchors.locate(qs[0]['text'],a['quote'])

    def test_multiple_anchors_are_separate_and_range_includes_middle(self):
        view,qs=self.reading('甲出发。中间原文。乙归来。');ss=anchors.aliased_segments(qs)[qs[0]['id']]
        value=g.mock(view,g.EVIDENCE_32K);o=value['source_observations'][0]
        o['anchors']=[{'evidence_id':'E001','start_segment_id':s['id'],'end_segment_id':s['id']} for s in (ss[0],ss[-1])]
        accepted=g.validate_value(value,view,qs,g.EVIDENCE_32K)
        self.assertEqual([a['quote'] for a in accepted['source_observations'][0]['anchors']],['甲出发。','乙归来。'])
        o['anchors']=[{'evidence_id':'E001','start_segment_id':ss[0]['id'],'end_segment_id':ss[-1]['id']}]
        accepted=g.validate_value(value,view,qs,g.EVIDENCE_32K)
        self.assertEqual(accepted['source_observations'][0]['anchors'][0]['quote'],qs[0]['text'])

    def test_bad_ids_positions_and_quote_output_never_repaired(self):
        view,qs=self.reading();value=g.mock(view,g.EVIDENCE_32K)
        for change in ({'evidence_id':'E999'},{'start_segment_id':'foreign'},
                       {'start_segment_id':value['source_observations'][0]['anchors'][0]['end_segment_id'],
                        'end_segment_id':value['source_observations'][0]['anchors'][0]['start_segment_id']},
                       {'quote':'模型不能抄写'}):
            bad=deepcopy(value);bad['source_observations'][0]['anchors'][0].update(change)
            with self.assertRaises((ValueError,ValidationError)):g.validate_value(bad,view,qs,g.EVIDENCE_32K)
        bad=deepcopy(view);bad['evidence'][0]['text']='换成别的原文'
        with self.assertRaises(ValueError):g.validate_value(value,bad,qs,g.EVIDENCE_32K)
        whitespace=anchors.aliased_segments(qs)[qs[0]['id']][2]['id']
        bad=deepcopy(value);bad['source_observations'][0]['anchors']=[{'evidence_id':'E001',
            'start_segment_id':whitespace,'end_segment_id':whitespace}]
        with self.assertRaises(ValueError):g.validate_value(bad,view,qs,g.EVIDENCE_32K)
        old=v2.mock(g.aliases(view)[0])
        with self.assertRaises(ValidationError):g.decode(json.dumps(old),view,qs,g.EVIDENCE_32K)

    def test_synthesis_inherits_only_stable_verified_anchor_id(self):
        view,qs=self.reading();accepted=g.validate_value(g.mock(view,g.EVIDENCE_32K),view,qs,g.EVIDENCE_32K)
        child=accepted|{'card_id':'child','level':'section','section':1}
        synth=v2.synthesis_view('fixture',[child],{},preserve_locations=True)
        selected=g.model_view(synth,g.EVIDENCE_32K);value=g.mock(synth,g.EVIDENCE_32K)
        self.assertEqual(selected['children'][0]['source_observations'][0]['anchors'],[{'anchor_id':'A001'}])
        self.assertNotIn('quote',value['source_observations'][0]['anchors'][0])
        parent=g.validate_value(value,synth,qs,g.EVIDENCE_32K)
        self.assertEqual(parent['source_observations'][0]['anchors'],accepted['source_observations'][0]['anchors'])
        identity=anchors.anchor_id(parent['source_observations'][0]['anchors'][0])
        grand=v2.synthesis_view('fixture',[parent|{'card_id':'parent','level':'section','section':1}],{},preserve_locations=True)
        self.assertEqual(next(iter(anchors.inherited(grand))),identity)
        for a in ({'anchor_id':'A999'},{'anchor_id':'A001','quote':'缩短'}):
            bad=deepcopy(value);bad['source_observations'][0]['anchors']=[a]
            with self.assertRaises((ValueError,ValidationError)):g.validate_value(bad,synth,qs,g.EVIDENCE_32K)

    def test_structured_selectors_survive_without_invented_canonical_offsets(self):
        s=self.sources([('角色语音',[('structured_observation',{'speaker':'甲','text':'未曾归来。'})]),
                        ('剧情对话',[('dialogue_graph',None)])])
        source=next(iter(s.documents));plan=structure.plan(s,source)
        for _,view,qs in structure.batches(s,source,plan):
            value=g.validate_value(g.mock(view,g.EVIDENCE_32K),view,qs,g.EVIDENCE_32K)
            for a in value['source_observations'][0]['anchors']:
                q=next(q for q in qs if q['id']==a['evidence_id'])
                self.assertNotIn('canonical_start',q)
                self.assertEqual(s.quote(q['unit_id'],a['ru_start'],a['ru_end'])['text'],a['quote'])
            self.assertTrue(all('selector' in e for e in g.model_view(view,g.EVIDENCE_32K)['evidence']))

    def test_fixed_parameters_capacity_and_separate_semantic_status(self):
        view,qs=self.reading();wire=g.wire(view,g.EVIDENCE_32K)
        self.assertEqual((wire['max_tokens'],wire['stream'],wire['extra_body'],wire['response_format']),
            (32000,False,{'thinking':{'type':'disabled'}},{'type':'json_object'}))
        limit=len(json.dumps(wire,ensure_ascii=False))-1
        self.assertFalse(structure.capacity(view,limit,strategy=g.EVIDENCE_32K))
        self.assertTrue(structure.capacity(view,limit+1,strategy=g.EVIDENCE_32K))
        obj={'id':'mock','object':'chat.completion','model':g.wire(view,g.EVIDENCE_32K)['model'],'created':1,
             'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(g.mock(view,g.EVIDENCE_32K))}}],
             'usage':{'prompt_tokens':1,'completion_tokens':2,'total_tokens':3}}
        result,value=g.assess(json.dumps(obj),obj,view,qs,g.EVIDENCE_32K)
        self.assertEqual(result['reference_validation']['status'],'PASS')
        self.assertEqual(result['semantic_quality'],'NOT_VALIDATED')
        obj['usage']={'prompt_tokens':1,'completion_tokens':32001,'total_tokens':32002}
        result,_=g.assess(json.dumps(obj),obj,view,qs,g.EVIDENCE_32K)
        self.assertEqual(result['output_limit']['status'],'EXCEEDED')

    def test_preparation_raw_first_maps_resume_and_old_schema_compatibility(self):
        root=self.prepared();config=read(root/'prepared/config.json')
        self.assertEqual(config['generation_strategy'],g.EVIDENCE_32K)
        self.assertEqual(config['quote_locations_sha256'],sha(root/'prepared/quote-locations.json'))
        result=build.run(root,max_new_requests=100);self.assertEqual(result['new_sdk_calls'],2)
        for p in root.glob('jobs/*/plan.json'):
            plan=read(p);self.assertIn('quote_locations',plan)
            self.assertEqual(plan['wire']['max_tokens'],32000)
        self.assertEqual(build.audit(root)['archive_integrity'],'PASS')
        before={p.as_posix():sha(p) for p in root.glob('jobs/*/complete.json')}
        with patch.object(build,'code_identity',return_value={}):resumed=build.run(root)
        self.assertEqual(resumed['new_sdk_calls'],0)
        self.assertEqual(before,{p.as_posix():sha(p) for p in root.glob('jobs/*/complete.json')})
        self.assertEqual(g.schema(g.DEEPSEEK_32K),v2.SCHEMA)
        self.assertEqual(g.output_cap(g.DEEPSEEK),12288)

    def test_partial_export_later_completion_new_index_and_reuse(self):
        root=self.prepared();build.run(root,max_new_requests=1)
        enc=MockEncoder();out=self.root/'index-one';first=build.build_index(root,out,enc)
        selection=first['card_export']
        self.assertEqual((selection['selected_source_count'],selection['skipped_source_count'],selection['card_count']),(1,1,1))
        self.assertEqual(len(list(build.iter_cards(root))),1)
        self.assertEqual(read(out/'card-selection.json'),selection);verify(out)
        before={p.as_posix():sha(p) for p in root.glob('documents/*/attempt-*/cards.json')}
        with patch.object(enc,'encode_documents',side_effect=AssertionError('No repeated encoding')):
            build.build_index(root,out,enc)
        self.assertEqual(before,{p.as_posix():sha(p) for p in root.glob('documents/*/attempt-*/cards.json')})
        build.run(root,max_new_requests=100)
        with self.assertRaises(ValueError):build.build_index(root,out,enc)
        with patch.object(enc,'encode_documents',wraps=enc.encode_documents) as encode:
            second=build.build_index(root,self.root/'index-two',enc,reuse_from=out)
        self.assertEqual(second['card_export']['selected_source_count'],2)
        self.assertEqual(sum(len(c.args[0]) for c in encode.call_args_list),1)

    def test_empty_export_never_loads_encoder(self):
        root=self.prepared();enc=MockEncoder()
        with patch.object(enc,'load',side_effect=AssertionError('No encoding')):
            result=build.build_index(root,self.root/'empty',enc)
            self.assertEqual(result['status'],'EMPTY_NO_ELIGIBLE_CARDS')
            self.assertEqual(build.build_index(root,self.root/'empty',enc),result)
        self.assertEqual(result['card_export']['skipped_source_count'],2)
        self.assertFalse((self.root/'empty/spec.json').exists())

    def test_empty_cli_never_starts_embedding_worker(self):
        from genshin_corpus.retrieval.navi_offline import __main__ as cli, embedding
        root=self.prepared();stdout=io.StringIO()
        with patch.object(embedding,'LocalEncoderProcess',side_effect=AssertionError('No worker')),\
             patch.object(sys,'argv',['navi','card-index','--run',str(root),'--out',str(self.root/'cli-empty')]),\
             redirect_stdout(stdout):cli.main()
        self.assertEqual(json.loads(stdout.getvalue())['status'],'EMPTY_NO_ELIGIBLE_CARDS')

    def test_corrupt_source_skipped_but_corrupt_preparation_blocks(self):
        root=self.prepared();build.run(root,max_new_requests=100)
        target=sorted(root.glob('documents/*/attempt-*/cards.json'))[0]
        with target.open('a',encoding='utf8') as handle:handle.write(' ')
        selection=build.card_selection(root)
        self.assertEqual((selection['selected_source_count'],selection['skipped_source_count']),(1,1))
        self.assertEqual(selection['skipped_sources'][0]['status'],'ISOLATED_INVALID_SOURCE')
        with (root/'prepared/config.json').open('a',encoding='utf8') as handle:handle.write(' ')
        with self.assertRaises(ValueError):build.card_selection(root)


if __name__=='__main__':unittest.main()
