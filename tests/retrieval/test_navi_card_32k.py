"""Offline acceptance of the new Card campaign, including failure dependencies."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import httpx
from genshin_corpus.retrieval.navi_offline import card_generation as g, cards_v2 as v2
from genshin_corpus.retrieval.navi_offline import card_build as build, card_structure as structure
from genshin_corpus.retrieval.navi_offline.common import read, sha
from tests.retrieval.test_navi_offline import fixture
from tests.retrieval.test_navi_link_delivery import LinkDeliveryTests


def transport(obj,status=200):
    class Body(httpx.SyncByteStream):
        def __iter__(self):yield json.dumps(obj).encode()
    return httpx.MockTransport(lambda req:httpx.Response(status,headers={'content-type':'application/json'},stream=Body()))


class Card32kTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'));p.start();self.addCleanup(p.stop)
        self.cp,self.rp=fixture(self.root)

    def prepare(self,**kwargs):
        root=self.root/'run';build.prepare(root,self.cp,self.rp,['0'],total_request_limit=100,**kwargs);return root

    def failed(self,view,finish='length',text='{"overview":',usage=True,strategy=g.EVIDENCE_32K):
        return {'id':'failed','object':'chat.completion','model':g.wire(view,strategy)['model'],'created':1,
            'choices':[{'index':0,'finish_reason':finish,'message':{'role':'assistant','content':text}}],
            'usage':{'prompt_tokens':1,'completion_tokens':2,'total_tokens':3} if usage else None}

    def test_parameters_caps_and_failure_categories(self):
        view={'evidence':[],'children':[],'mode':'read','title':'fixture'}
        wire=g.wire(view,g.DEEPSEEK_32K)
        self.assertEqual((wire['max_tokens'],wire['stream'],wire['extra_body'],wire['response_format']),
            (32000,False,{'thinking':{'type':'disabled'}},{'type':'json_object'}))
        self.assertEqual(g.wire(view,g.DEEPSEEK)['max_tokens'],12288)
        for text,finish,expected in [('{"overview":','length','JSON_TRUNCATED'),
                ('We need produce JSON schema. '*100,'length','VISIBLE_PLANNING_LEAK'),
                ('nonsense','stop','JSON_INVALID'),('{}','stop','SCHEMA_INVALID')]:
            obj=self.failed(view,finish,text,strategy=g.DEEPSEEK_32K);result,_=g.assess(json.dumps(obj),obj,view,[],g.DEEPSEEK_32K)
            self.assertEqual(result['card_failure'],expected)
        obj=self.failed(view,'stop',json.dumps(v2.mock(view)),strategy=g.DEEPSEEK_32K)
        for n,expected in [(32000,'PASS'),(32001,'FAIL')]:
            obj['usage']={'prompt_tokens':1,'completion_tokens':n,'total_tokens':n+1}
            result,_=g.assess(json.dumps(obj),obj,view,[],g.DEEPSEEK_32K)
            self.assertEqual(result['protocol_validation']['status'],expected)

    def test_structure_order_small_volumes_and_capacity(self):
        self.root=self.root/'structure';self.root.mkdir()
        sources=LinkDeliveryTests.sources(self,[('第一卷',[('rich_text','甲出发。')]),('第二卷',[('rich_text','乙归来。')])])
        source=next(iter(sources.documents));record=sources.record(source)
        record['record_metadata']['source_template_layout']={'tab':[{'module_group':[{'module':[{'id':'b'},{'id':'a'}]}]}]}
        record['sections'][0]['source_metadata']['source_module_id']='a'
        record['sections'][1]['source_metadata']['source_module_id']='b'
        plan=structure.plan(sources,source)
        batches=list(structure.batches(sources,source,plan))
        self.assertEqual([s for s,_,_ in batches],[1,0])
        self.assertFalse(structure.capacity(batches[0][1],100000,32001))
        self.assertTrue(structure.capacity(batches[0][1],100000,100000))

    def test_one_reorganization_multiple_calls_resume_and_temporary_nodes(self):
        root=self.prepare();normal=build.mock_transport;seen=[]
        def first_bad(view,strategy):
            seen.append(view['mode'])
            return transport(self.failed(view)) if len(seen)==1 else normal(view,strategy)
        with patch.object(build,'mock_transport',side_effect=first_bad):
            first=build.run(root,max_new_requests=2)
            self.assertEqual(first['sources'][0]['status'],'PARTIAL_REQUIRES_TARGETED_ACTION')
            second=build.run(root,max_new_requests=50)
        self.assertEqual(second['sources'][0]['status'],'COMPLETE')
        self.assertEqual(len(list((root/'reorganizations').glob('*/decision.json'))),1)
        self.assertEqual(len(seen),4)
        self.assertEqual(build.run(root)['new_sdk_calls'],0)
        self.assertEqual(build.audit(root)['archive_integrity'],'PASS')
        cards=list(build.iter_cards(root));self.assertTrue(cards)
        self.assertTrue(all(c['node_role']=='SEMANTIC_CARD' for c in cards))
        doc=next(c for c in cards if c['level']=='document')
        self.assertEqual(doc['coverage']['direct_delivered_ids'],[])
        self.assertTrue(doc['coverage']['inherited_evidence_ids'])

    def test_recovery_subtree_cannot_reorganize_again(self):
        root=self.prepare()
        with patch.object(build,'mock_transport',side_effect=lambda view,strategy:transport(self.failed(view))):
            result=build.run(root,max_new_requests=50)
        self.assertEqual(result['sources'][0]['status'],'PARTIAL_REQUIRES_TARGETED_ACTION')
        self.assertEqual(len(list((root/'reorganizations').glob('*/decision.json'))),1)
        self.assertFalse(list(root.glob('documents/*/accepted-build.json')))
        for p in root.glob('documents/*/attempt-*/cards.json'):
            self.assertFalse(any(c['level']=='document' for c in read(p)['cards']))

    def test_schema_and_unknown_do_not_reorganize_and_independent_source_continues(self):
        for status in (200,524):
            root=self.root/str(status);build.prepare(root,self.cp,self.rp,['0','1'],total_request_limit=100)
            normal=build.mock_transport;calls=[]
            def failing(view,strategy):
                calls.append(view['title'])
                if len(calls)==1:return transport(self.failed(view,'stop','{}',status==200),status)
                return normal(view,strategy)
            with patch.object(build,'mock_transport',side_effect=failing):result=build.run(root,max_new_requests=50)
            self.assertEqual([s['status'] for s in result['sources']],['PARTIAL_REQUIRES_TARGETED_ACTION','COMPLETE'])
            self.assertFalse(list((root/'reorganizations').glob('*')))

    def test_verified_archived_runtime_can_resume(self):
        root=self.prepare(generation_strategy=g.DEEPSEEK);before=sha(root/'prepared/complete.json')
        with patch.object(build,'code_identity',return_value={}):result=build.run(root,max_new_requests=50)
        self.assertEqual(result['sources'][0]['status'],'COMPLETE')
        with patch.object(build,'code_identity',return_value={}):
            self.assertEqual(build.audit(root)['archive_integrity'],'PASS')
        self.assertEqual(sha(root/'prepared/complete.json'),before)

    def test_semantic_scope_survives_capacity_partition_and_partial_has_missing_ranges(self):
        card={'source_id':'s','canonical_sha256':'snapshot','level':'document','section':None,'overview':'summary',
            'evidence':[{'unit_id':'u','ru_start':0,'ru_end':10}]}
        split=deepcopy(card);split['evidence']=[{'unit_id':'u','ru_start':0,'ru_end':4},
            {'unit_id':'u','ru_start':4,'ru_end':10}]
        self.assertEqual(structure.mark(card)['semantic_scope_id'],structure.mark(split)['semantic_scope_id'])
        root=self.prepare(body_limit=8);build.run(root,max_new_requests=1)
        saved=read(next(root.glob('documents/*/attempt-*/cards.json')))
        self.assertTrue(saved['missing_required_reading'])
        self.assertFalse(any(c['level']=='document' for c in saved['cards']))


if __name__=='__main__':unittest.main()
